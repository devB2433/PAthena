"""Application adapter: native Mantis owns code analysis and finding decisions."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from .config import Settings, contained_file
from .domain import Fact, Finding, Risk, StageOutput, Threat, new_id
from .mantis_paths import native_source_path
from .skills import digest
from .localization import policy_hashes, tr
from .store import BudgetExceeded, Store
from .provider import ProviderUnavailable, ProviderBlocked, OutputContractError

ENGINE = Path(__file__).parent / "vendor/mantis"


def engine_fingerprint() -> str:
    manifest = json.loads((ENGINE / "SOURCE.json").read_text())
    for name, entry in manifest["files"].items():
        if digest((ENGINE / name).read_bytes()) != entry["sha256"]:
            raise ValueError("Mantis 引擎版本或来源摘要不一致")
    return digest((ENGINE / "SOURCE.json").read_bytes() + Path(__file__).read_bytes()
                  + (Path(__file__).parent / "mantis_worker.py").read_bytes()
                  + (Path(__file__).parent / "mantis_paths.py").read_bytes()
                  + (Path(__file__).parent / "model_contract.py").read_bytes()
                  + (Path(__file__).parent / "provider.py").read_bytes()
                  + json.dumps(policy_hashes(), sort_keys=True).encode())


class MantisEngine:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store
        self.fingerprint = engine_fingerprint()

    def workspace(self, run_id: str) -> Path:
        return self.settings.state_dir.resolve() / "mantis" / run_id

    async def invoke(self, run_id: str, job: dict, key: str) -> dict:
        work = self.workspace(run_id)
        work.mkdir(parents=True, exist_ok=True)
        job.update({"work": str(work), "run_id": run_id,
                    "snapshot_id": digest(json.dumps(self.store.run(run_id)["snapshot"], sort_keys=True).encode()),
                    "engine_hash": self.fingerprint})
        input_path, output_path = work / f"{key}-input.json", work / f"{key}-output.json"
        encoded = json.dumps(job, ensure_ascii=False)
        if job.get("phase") and output_path.exists():
            try:
                cached = json.loads(output_path.read_text())
            except (ValueError, OSError):
                cached = {}
            if cached.get("job_hash") == digest(encoded.encode()) and not cached.get("error"):
                return cached
        input_path.write_text(encoded)
        output_path.unlink(missing_ok=True)
        env = {k: v for k, v in os.environ.items() if not any(s in k for s in ("KEY", "TOKEN", "MANTIS", "LLM_"))}
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "security_auditor.mantis_worker", str(input_path), str(output_path),
            env=env, cwd=str(self.settings.state_dir.resolve()),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=3700 if job.get("phase") else 45)
        except BaseException:
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        if process.returncode or not output_path.exists():
            raise ValueError("Mantis 进程未完成，未生成分析结果")
        result = json.loads(output_path.read_text())
        if result.get("error") == "budget":
            raise BudgetExceeded(result["message"])
        for error_class in (ProviderUnavailable, ProviderBlocked, OutputContractError):
            if result.get("error") == error_class.__name__:
                raise error_class(result["message"], code=result.get('code'))
        if result.get("error"):
            raise ValueError(result["message"] + "（" + result.get("exception_type", "") + "）")
        return result

    async def execute(self, run_id: str, phase: str, parent: str):
        run = self.store.run(run_id)
        if engine_fingerprint() != run["snapshot"].get("mantis_hash"):
            raise ValueError("Mantis 版本已变化，需要新运行")
        tid = self.store.add_task(run_id, "mantis_" + phase, {"engine": self.fingerprint}, parent, "engine")
        if self.store.task(tid)["status"] == "SUCCEEDED":
            return
        self.store.start_task(tid, instruction_hash=self.fingerprint)
        try:
            result = await self.invoke(run_id, {
                "phase": phase, "task_id": tid, "repository": run["snapshot"]["repository"],
                "application_db": str(self.store.path.resolve()), "gateway": self.settings.gateway,
                "model_id": self.settings.model_id, "language": run["language"],
            }, phase)
            output = self.map_output(run_id, tid, result)
            # Native exports are retained even when the product mapping finds missing citations.
            self.store.commit_output(tid, "mantis_engine", output)
        except BudgetExceeded:
            self.store.fail_task(tid, "累计预算不足或用户暂停", "PENDING")
            raise
        except Exception:
            self.store.fail_task(tid, "Mantis 执行或产物映射未完成")
            raise

    def map_output(self, run_id: str, task_id: str, result: dict) -> StageOutput:
        selected = self.store.run(run_id)["language"]
        work = self.workspace(run_id)
        refs = []
        reads = result["reads"]
        for read in reads:
            if read["end"] < read["start"]:
                continue
            read = {**read, "path": native_source_path(work / "snapshot", read["path"])}
            path = contained_file(work / "snapshot", read["path"])
            lines = path.read_text(errors="replace").splitlines()
            lo, hi = read["start"], read["end"]
            eid = self.store.add_evidence(
                run_id, "code", f"{read['path']}:{lo}-{hi}", "\n".join(lines[lo - 1:hi]),
                {"path": read["path"], "line_start": lo, "line_end": hi,
                 "source_hash": digest(path.read_bytes()), "engine": "mantis"},
            )
            self.store.note_read(task_id, eid)
            refs.append((read, eid))
        records, gaps = [], list(result.get("gaps", []))
        all_refs = list(dict.fromkeys(eid for _, eid in refs))

        def identifier(kind, key):
            return "mantis_" + digest(f"{run_id}:{kind}:{key}".encode())[:32]

        for artifact in result["artifacts"]:
            if artifact["artifact_type"] not in {"summary", "threat_model"} or result["phase"] != "model":
                continue
            try:
                data = json.loads(artifact["content"])
            except ValueError:
                gaps.append(tr('Mantis 模型产物不是结构化对象，原件已保留', selected))
                continue
            if not all_refs:
                gaps.append(tr('Mantis 未记录可验证的源码读取，模型产物未映射为已支持结论', selected))
                continue
            if artifact["artifact_type"] == "summary":
                if not str(data.get("overview", "")).strip():
                    gaps.append(tr('Mantis 结构化系统摘要为空，原生知识库保留；未映射为空的系统模型记录', selected))
                    continue
                records.append(Fact(
                    id=identifier("summary", artifact["filepath"]), title=tr('Mantis 代码系统模型', selected),
                    module=tr('系统', selected), fact_type="MODULE", basis="OBSERVED", evidence_ids=all_refs,
                    rationale=(data.get("overview") or json.dumps(data, ensure_ascii=False))[:10000],
                    engine="mantis", native_data=data,
                ))
            else:
                for index, text in enumerate(data.get("threats", [])):
                    fields = dict(re.findall(r"(?:^|[;|]\s*)([a-z_]+):\s*([^;|]+)", text))
                    records.append(Threat(
                        id=identifier("threat", index), title=fields.get("title", text)[:500],
                        module=fields.get("target_component", tr('系统', selected)),
                        attacker="；".join(data.get("threat_actors", [])) or tr('未单独声明', selected),
                        entrypoint=fields.get("attack_vector", tr('未单独声明', selected)),
                        trust_boundary="；".join(data.get("trust_boundaries", [])) or tr('未单独声明', selected),
                        preconditions=[], impact=fields.get("impact", tr('未单独声明', selected)),
                        evidence_ids=all_refs, rationale=text[:10000], engine="mantis",
                        native_data={"threat": text, "model": data},
                    ))
        if result["phase"] == "audit":
            for native in result["findings"]:
                status = str(native["status"]).lower()
                if status in {"duplicate", "superseded", "merged"}:
                    continue  # Originals and lineage stay in the native database/export.
                locations = [(native["filepath"], n) for n in (native.get("line_numbers") or [])]
                for cp in native.get("code_paths", []):
                    parsed = re.fullmatch(r"(.+):(\d+)", cp)
                    if parsed:
                        locations.append((parsed[1], int(parsed[2])))
                evidence = list(dict.fromkeys(
                    eid for read, eid in refs for path, line in locations
                    if path == read["path"] and read["start"] <= line <= read["end"]
                ))
                if not evidence:
                    gaps.append(f"Mantis 发现 {native['id']} 的源码引用缺少实际读取记录，原始发现已保留")
                    continue
                fid = identifier("finding", native["id"])
                state = ("FALSE_POSITIVE" if status in {"false_positive", "non_viable"}
                         else "STATIC_SUPPORTED" if status == "static_confirmed" else "NEEDS_EVIDENCE")
                records.append(Finding(
                    id=fid, title=(native.get("title") or tr('Mantis 发现', selected))[:500],
                    module=str(Path(native["filepath"]).parent), finding_type="STATIC_VULNERABILITY",
                    status=state, evidence_ids=evidence,
                    impact=native.get("description") or tr('未单独声明', selected),
                    recommendation=native.get("remediation") or tr('未提供修复建议', selected),
                    rationale=(native.get("triage_reasoning") or native.get("description") or tr('Mantis 原生发现', selected))[:10000],
                    engine="mantis", native_id=str(native["id"]), native_status=status,
                    native_data={**native, "verdicts": result["verdicts"]},
                ))
                if native.get("mantis_risk_score") is not None:
                    records.append(Risk(
                        id=identifier("risk", native["id"]), title=tr('Mantis 风险评级', selected), subject_id=fid,
                        severity=native["severity"],
                        evidence_strength="STATIC_SUPPORTED" if state == "STATIC_SUPPORTED" else "UNKNOWN",
                        impact_score=native["impact_score"], likelihood_score=native["likelihood_score"],
                        native_score=native["mantis_risk_score"], evidence_ids=evidence,
                        rationale=(native.get("triage_reasoning") or tr('采用 Mantis 原生风险校准', selected))[:10000],
                        engine="mantis", native_id=str(native["id"]), native_data=native,
                    ))
        return StageOutput(records=records, gaps=gaps, summary=tr('Mantis 原生静态工作流产物', selected))

    async def navigate(self, run_id: str, operation: str, args: dict, task_id: str) -> dict:
        result = await self.invoke(run_id, {"operation": operation, "args": args}, "query_" + new_id())
        if result.get("read"):
            read = result["read"]
            path = contained_file(self.workspace(run_id) / "snapshot", read["path"])
            lines = path.read_text(errors="replace").splitlines()
            lo, hi = read["start"], read["end"]
            eid = self.store.add_evidence(run_id, "code", f"{read['path']}:{lo}-{hi}",
                                          "\n".join(lines[lo - 1:hi]),
                                          {"path": read["path"], "line_start": lo, "line_end": hi,
                                           "source_hash": digest(path.read_bytes()), "engine": "mantis"})
            self.store.note_read(task_id, eid)
            result["evidence_id"] = eid
        return result
