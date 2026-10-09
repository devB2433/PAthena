from __future__ import annotations

import asyncio
import csv
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .config import Settings
from .domain import StrictModel, new_id
from .ingestion import repository_inventory, standard_manifest
from .runtime import Scheduler, report, result_matrix
from .product_report import product_report
from .skills import digest
from .store import Store
from .localization import tr, policy_hashes
from .provider import MESSAGES


class ProjectRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)


class BudgetRequest(StrictModel):
    max_requests: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)


class LanguageRequest(StrictModel):
    language: str = Field(pattern='^(en|zh-CN)$')


class RunRequest(StrictModel):
    mode: str = Field(default="requirements_only", pattern="^(full|requirements_only|code_only|implementation_only)$")
    repository_id: str | None = None
    baseline_run_id: str | None = None
    budget: BudgetRequest | None = None


def create_app(settings: Settings | None = None, executor=None, mantis_executor=None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.database, enforce_budgets=settings.enforce_budgets)
    scheduler = Scheduler(settings, store, executor, mantis_executor)

    @asynccontextmanager
    async def lifespan(app):
        worker = asyncio.create_task(scheduler.serve())
        yield
        scheduler.stop.set()
        await worker

    app = FastAPI(title="安全设计审查", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.scheduler, app.state.settings = store, scheduler, settings

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "testserver", "analysis-service"}:
            return JSONResponse({"detail": "桌面版本仅接受本地访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.url.netloc:
            return JSONResponse({"detail": "不接受跨站请求"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": tr("项目或资源不存在", store.language(settings.language))}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": tr(str(exc), store.language(settings.language))}, status_code=400)

    def scoped_run(project_id: str, run_id: str) -> dict:
        run = store.run(run_id)
        if run["project_id"] != project_id:
            raise KeyError("资源不属于此项目")
        return public_run(run)

    def current_versions():
        return {'mantis_hash': scheduler.mantis.fingerprint, 'workflow_hash': digest(settings.workflow.read_bytes()),
                'skill_hashes': {item['id']: item['sha256'] for item in scheduler.loader.catalog()},
                'language_policy_hashes': policy_hashes()}

    def public_run(run):
        policy, usage = run.get('budget'), run['usage']
        exhausted = bool(policy and ((policy['max_requests'] is not None and usage['requests'] >= policy['max_requests']) or
                                    (policy['max_tokens'] is not None and usage['tokens'] >= policy['max_tokens'])))
        if not policy and not run['unlimited_budget']:
            exhausted = usage['requests'] >= run['max_requests'] or usage['tokens'] >= run['max_tokens']
        exhausted = settings.enforce_budgets and exhausted
        changed = any(k in run['snapshot'] and run['snapshot'][k] != v for k, v in current_versions().items()
                      if k != 'mantis_hash' or run['snapshot'].get('repository'))
        run['controls'] = {
            'can_resume': run['status'] in {'PAUSED', 'FAILED'} and not exhausted and not changed,
            'can_update_budget': settings.enforce_budgets and bool(policy) and run['status'] in {'PAUSED', 'FAILED', 'PENDING'},
            'budget_exhausted': exhausted,
            'requires_new_run': changed and run['status'] in {'PAUSED', 'FAILED'},
        }
        run['repository_id'] = (run['snapshot'].get('repository') or {}).get('id')
        selected = store.language(settings.language)
        if run.get('issue'):
            issue = run['issue']
            run['display_error'] = tr(MESSAGES.get(issue['code'], issue['message']), selected)
        else:
            run['display_error'] = tr(run.get('error') or '', selected)
        return run

    def snapshot(project_id: str, request: RunRequest) -> dict:
        continuation = request.mode == 'implementation_only'
        if bool(request.baseline_run_id) != continuation:
            raise ValueError('实现检查需要选择需求来源运行；其他模式不得指定需求来源')
        assets = [] if request.mode == "code_only" or continuation else store.rows(
            "SELECT * FROM assets WHERE project_id=? ORDER BY id", (project_id,)
        )
        if not assets and request.mode != "code_only" and not continuation:
            raise ValueError("请先上传设计材料")
        result = {
            "language": store.language(settings.language),
            "language_policy_hashes": policy_hashes(),
            "assets": assets,
            "repository": None,
            "standard": None,
            "skill_hashes": {item["id"]: item["sha256"] for item in scheduler.loader.catalog()},
            "workflow_hash": digest(settings.workflow.read_bytes()),
            "mantis_hash": scheduler.mantis.fingerprint,
            "standard_use_policy": "precomputed_controls_vector_matching_v3",
        }
        if continuation:
            from .baseline import baseline_snapshot
            result['baseline'] = baseline_snapshot(store, project_id, request.baseline_run_id)
            result['language'] = result['baseline']['language']
        if request.mode in {"full", "code_only", "implementation_only"}:
            if not request.repository_id or request.repository_id not in settings.repositories:
                raise ValueError("代码分析需要选择已登记的仓库")
            root = Path(settings.repositories[request.repository_id])
            result["repository"] = {
                "id": request.repository_id,
                "root": str(root.resolve()),
                "files": repository_inventory(root, settings.code_limit, code_only=request.mode in {"code_only", "implementation_only"}),
            }
            if not result["repository"]["files"]:
                raise ValueError("仓库没有可分析的源代码")
        if settings.standard_pack and request.mode != "code_only" and not continuation:
            result["standard"] = standard_manifest(Path(settings.standard_pack))
            if not result['standard'].get('prepared'):
                raise ValueError('标准尚未独立生成控制需求和预计算向量，请先准备标准库')
            from .embeddings import encoder
            if encoder(settings.embedding_model_dir).profile != result['standard']['prepared']['profile']:
                raise ValueError('本地查询模型与标准向量版本不一致，请先显式重建标准目录')
            expected_ranker = result['standard']['prepared'].get('retrieval', {}).get('reranker_profile')
            if expected_ranker:
                from .retrieval_models import reranker
                if reranker(settings.reranker_model_dir).profile != expected_ranker:
                    raise ValueError('本地重排模型与标准目录版本不一致，请先显式重建标准目录')
            from .standard_library import import_library
            result["standard"]["library_id"] = import_library(store, result["standard"])
        return result

    @app.get('/api/v1/standards')
    def standards():
        return {'items': store.rows('SELECT v.id,v.standard_id,v.version,v.clause_count,v.context_count,'
                                   'c.catalog_id,c.control_count,c.vector_count FROM standard_versions v '
                                   'LEFT JOIN standard_catalogs c ON c.library_id=v.id ORDER BY v.version,v.id,c.catalog_id')}

    @app.get("/api/v1/health")
    def health():
        return {
            "status": "ready",
            "version": "0.1.0",
            "static_only": True,
            "skills": len(scheduler.loader.catalog()),
        }

    @app.get("/api/v1/settings")
    def public_settings():
        return {
            "language": store.language(settings.language),
            "supported_languages": ['en', 'zh-CN'],
            "repositories": list(settings.repositories),
            "standard_configured": bool(settings.standard_pack),
            "model": settings.model_id,
            "version": "0.1.0",
            "incremental_available": False,
            "budget_enforced": settings.enforce_budgets,
            "default_budget": {
                "max_requests": (settings.max_requests or None) if settings.enforce_budgets else None,
                "max_tokens": (settings.max_tokens or None) if settings.enforce_budgets else None,
            },
        }

    @app.put("/api/v1/settings")
    def change_settings(body: LanguageRequest):
        store.set_language(body.language)
        return public_settings()

    @app.get("/api/v1/skills")
    def skills():
        return {"items": scheduler.loader.catalog()}

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: ProjectRequest):
        return store.create_project(body.name)

    @app.get("/api/v1/projects")
    def projects():
        return {"items": store.rows("SELECT * FROM projects ORDER BY created DESC")}

    @app.get("/api/v1/projects/{project_id}/assets")
    def assets(project_id: str):
        store.project(project_id)
        return {
            "items": store.rows(
                "SELECT id,name,sha256,media_type,created FROM assets WHERE project_id=?", (project_id,)
            )
        }

    @app.post("/api/v1/projects/{project_id}/documents", status_code=201)
    async def upload(project_id: str, file: UploadFile = File()):
        store.project(project_id)
        filename = Path(file.filename or "document").name
        if Path(filename).suffix.lower() not in {".docx", ".pdf", ".pptx", ".md", ".txt"}:
            raise ValueError("请上传 DOCX、PDF、PPTX、Markdown 或文本")
        path = settings.state_dir / "uploads" / project_id / (new_id() + Path(filename).suffix.lower())
        path.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        try:
            with path.open("wb") as target:
                while data := await file.read(64 * 1024):
                    total += len(data)
                    if total > settings.upload_limit:
                        raise HTTPException(413, "材料超过 25 MB 限额")
                    target.write(data)
            if not total:
                raise ValueError("材料为空")
            return store.add_asset(
                project_id, filename, path, file.content_type or "application/octet-stream"
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @app.post("/api/v1/projects/{project_id}/runs", status_code=202)
    def start_run(project_id: str, body: RunRequest, idempotency_key: str | None = Header(default=None)):
        return public_run(store.create_run(
            project_id,
            body.mode,
            snapshot(project_id, body),
            False,
            (body.budget.max_requests or 0) if body.budget else settings.max_requests,
            (body.budget.max_tokens or 0) if body.budget else settings.max_tokens,
            idempotency_key,
            physical=executor is None and mantis_executor is None,
        ))

    @app.get("/api/v1/projects/{project_id}/runs")
    def runs(project_id: str):
        store.project(project_id)
        return {
            "items": [
                public_run(store.run(row["id"]))
                for row in store.rows(
                    "SELECT id FROM runs WHERE project_id=? ORDER BY created DESC", (project_id,)
                )
            ]
        }

    @app.get("/api/v1/projects/{project_id}/runs/{run_id}")
    def get_run(project_id: str, run_id: str):
        return scoped_run(project_id, run_id)

    @app.get("/api/v1/projects/{project_id}/runs/{run_id}/tasks")
    def tasks(project_id: str, run_id: str):
        scoped_run(project_id, run_id)
        return {"items": store.rows("SELECT * FROM tasks WHERE run_id=? ORDER BY created,id", (run_id,))}

    @app.get("/api/v1/projects/{project_id}/runs/{run_id}/records")
    def records(project_id: str, run_id: str, kind: str | None = None):
        scoped_run(project_id, run_id)
        return {"items": store.records(run_id, kind)}

    @app.get("/api/v1/projects/{project_id}/runs/{run_id}/matrix")
    def matrix(project_id: str, run_id: str):
        scoped_run(project_id, run_id)
        return result_matrix(store, run_id)

    @app.get("/api/v1/projects/{project_id}/runs/{run_id}/evidence")
    def evidence_list(project_id: str, run_id: str):
        scoped_run(project_id, run_id)
        return {"items": store.evidence(run_id)}

    @app.get("/api/v1/projects/{project_id}/evidence/{evidence_id}")
    def evidence_detail(project_id: str, evidence_id: str):
        rows = store.rows(
            "SELECT run_id FROM evidence WHERE id=? AND project_id=?", (evidence_id, project_id)
        )
        if not rows:
            raise KeyError("证据不存在")
        return store.read_evidence(rows[0]["run_id"], evidence_id)

    @app.post("/api/v1/projects/{project_id}/runs/{run_id}/pause", status_code=202)
    def pause(project_id: str, run_id: str):
        return public_run(store.pause_run(project_id, run_id))

    @app.post("/api/v1/projects/{project_id}/runs/{run_id}/resume", status_code=202)
    def resume(project_id: str, run_id: str):
        return public_run(store.resume_run(project_id, run_id, current_versions()))

    @app.post("/api/v1/projects/{project_id}/runs/{run_id}/budget")
    def update_budget(project_id: str, run_id: str, body: BudgetRequest):
        return public_run(store.update_budget(project_id, run_id, body.max_requests, body.max_tokens))

    @app.get("/api/v1/projects/{project_id}/runs/{run_id}/events")
    async def events(
        project_id: str, run_id: str, after: int = 0, last_event_id: str | None = Header(default=None)
    ):
        scoped_run(project_id, run_id)

        async def stream():
            cursor = max(after, int(last_event_id or 0))
            while True:
                rows = store.rows(
                    "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT 100", (run_id, cursor)
                )
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                if store.run(run_id)["status"] in {"COMPLETED", "FAILED", "PAUSED"} and not rows:
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/api/v1/projects/{project_id}/runs/{run_id}/reports/{format}")
    def export(project_id: str, run_id: str, format: str):
        scoped_run(project_id, run_id)
        data = report(store, run_id)
        if format == "json":
            return JSONResponse(
                data, headers={"Content-Disposition": f'attachment; filename="report-{run_id}.json"'}
            )
        if format == "csv":
            selected = data["run"]["language"]
            def label(value):
                return tr(value, selected)
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            from .product_report import STATUS, FINDING_STATUS, REQUIREMENT_REVIEW

            matrix = result_matrix(store, run_id)
            writer.writerow([label(s) for s in ["输出物", "需求编号", "标题", "结果", "说明"]])
            rows = []
            for req in matrix["requirements"]:
                detail = '；'.join([req['rationale'], *[r['rationale'] for r in req['requirement_reviews']]])
                rows.append([label("安全需求"), req["requirement_number"], req["statement"],
                             label(REQUIREMENT_REVIEW[req['requirement_review_status']]), detail])
            for threat in data["records"]:
                if threat["kind"] == "threat":
                    rows.append([label("威胁建模"), "", threat["title"], threat["impact"], threat["rationale"]])
            for artifact in data.get('model_artifacts', []):
                if artifact['artifact_type'] == 'threat_model':
                    for field, artifact_label in [('key_risks', '主要风险'), ('threat_actors', '攻击者'),
                                         ('trust_boundaries', '信任边界'), ('entry_points', '攻击入口')]:
                        rows.extend([tr('威胁建模', selected), '', tr(artifact_label, selected), '', value]
                                    for value in artifact['data'].get(field, []))
            for req in matrix["requirements"]:
                detail = "；".join(f"{c['acceptance_criterion']}：{label(STATUS[c['implementation_status']])}，"
                                  f"{c['rationale']}" for c in req["criterion_checks"])
                rows.append([label("需求实现情况"), req["requirement_number"], req["statement"],
                             label(STATUS[req["implementation_status"]]), detail])
            for finding in matrix["findings"]:
                rows.append([label("安全风险"), "、".join(finding["requirement_numbers"]), finding["title"],
                             label(FINDING_STATUS.get(finding["review_status"], "待确认")), finding["rationale"]])
            for cells in rows:
                writer.writerow(
                    ["'" + cell if cell.startswith(("=", "+", "-", "@")) else cell for cell in cells]
                )
            return Response(
                "\ufeff" + buffer.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="report.csv"'},
            )
        if format == "html":
            sources = [store.read_evidence(run_id, source["id"]) for source in store.evidence(run_id)]
            return HTMLResponse(
                product_report(store.project(project_id)["name"], data, result_matrix(store, run_id), sources)
            )
        raise ValueError("报告格式支持 json、csv、html")

    if settings.web_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dist, html=True), name="web")
    return app


app = create_app()
