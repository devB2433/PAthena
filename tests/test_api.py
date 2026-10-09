import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from security_auditor.api import create_app
from security_auditor.runtime import report
from security_auditor.store import Store
from fixture_executor import FixtureExecutor, FixtureMantis


def wait_run(client, project_id, run_id):
    for _ in range(150):
        run = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}").json()
        if run["status"] in {"COMPLETED", "FAILED", "PAUSED"}:
            return run
        time.sleep(0.02)
    raise AssertionError("run timed out")


@pytest.mark.parametrize("selected", ["en", "zh-CN"])
def test_full_flow_and_replay(fixture_settings, seed_run, selected):
    fixture_settings = replace(fixture_settings, language=selected)
    fixture_store = Store(fixture_settings.database, enforce_budgets=fixture_settings.enforce_budgets)
    app = create_app(fixture_settings, executor=FixtureExecutor(fixture_store),
                     mantis_executor=FixtureMantis(fixture_settings, fixture_store))
    with TestClient(app) as client:
        pid, rid = seed_run(client)
        result = wait_run(client, pid, rid)
        assert result["status"] == "COMPLETED", result
        data = report(app.state.store, rid)
        assert data["assurance"] == "STATIC_EVIDENCE_ONLY"
        assert data["compliance"] == "NOT_ASSESSED"
        assert {t["wave"] for t in data["tasks"]} >= {"stage", "engine"}
        assert {r["implementation_status"] for r in data["records"] if r["kind"] == "assessment"} == {
            "VIOLATED",
            "STATIC_SUPPORTED",
        }
        assert not data["incomplete_tasks"]
        matrix = client.get(f"/api/v1/projects/{pid}/runs/{rid}/matrix").json()
        requirements = [r for r in data["records"] if r["kind"] == "requirement"]
        assert [r["id"] for r in matrix["requirements"]] == [r["id"] for r in requirements]
        assert matrix["requirements"][0]["implementation_status"] == "VIOLATED"
        assert matrix["requirements"][0]["requirement_number"] == "SR-001"
        assert client.get(f"/api/v1/projects/{pid}/runs/{rid}/reports/json").json()["requirement_matrix"] == matrix["requirements"]
        csv_report = client.get(f"/api/v1/projects/{pid}/runs/{rid}/reports/csv").text
        assert csv_report.count(("Requirement implementation" if selected == "en" else "需求实现情况") + ",SR-001") == 1
        assert ("Supported by static review" if selected == "en" else "静态复核支持") in csv_report
        assert ("Findings" if selected == "en" else "安全风险") + "," in csv_report
        pci = next(t for t in data["tasks"] if t["stage"] == "pci_mapping")
        assert pci["status"] == "SKIPPED"
        assert pci["id"] in data["skipped_tasks"]
        before = len(app.state.store.records(rid))
        assert client.post(f"/api/v1/projects/{pid}/runs/{rid}/resume").status_code == 400
        assert len(app.state.store.records(rid)) == before
        for format in ["html", "csv", "json"]:
            assert client.get(f"/api/v1/projects/{pid}/runs/{rid}/reports/{format}").status_code == 200
        html_report = client.get(f"/api/v1/projects/{pid}/runs/{rid}/reports/html").text
        for section in (["1. Security requirements", "2. Threat modeling", "3. Requirement implementation", "4. Findings"] if selected == "en" else ["1. 安全需求", "2. 威胁建模", "3. 需求实现情况", "4. 安全风险"]):
            assert section in html_report
        assert "orders.py:1-5" in html_report
        assert "def export_orders" in html_report
        ev = data["records"][0]["evidence_ids"][0]
        assert client.get(f"/api/v1/projects/{pid}/evidence/{ev}").status_code == 200
        other = client.post("/api/v1/projects", json={"name": "other"}).json()["id"]
        assert client.get(f"/api/v1/projects/{other}/evidence/{ev}").status_code == 404
        assert client.get(f"/api/v1/projects/{other}/runs/{rid}").status_code == 404
        events = client.get(f"/api/v1/projects/{pid}/runs/{rid}/events").text
        assert "run_status" in events
        assert "COMPLETED" in events


def test_cross_site_and_upload_boundary(settings):
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/v1/projects", json={"name": "x"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        pid = client.post("/api/v1/projects", json={"name": "x"}).json()["id"]
        assert (
            client.post(
                f"/api/v1/projects/{pid}/documents", files={"file": ("evil.exe", b"binary")}
            ).status_code
            == 400
        )
        assert (
            client.post(
                f"/api/v1/projects/{pid}/runs", json={"mode": "full", "repository_id": "../../"}
            ).status_code
            == 400
        )


def test_code_only_ignores_documents_and_runs_only_native_engine(fixture_settings, monkeypatch):
    from security_auditor.ingestion import repository_inventory
    from security_auditor.skills import digest

    root = Path(fixture_settings.repositories["fixture"])
    (root / "README.md").write_text("must not read")
    (root / "samples").mkdir()
    (root / "samples" / "document.pdf").write_bytes(b"not a valid pdf")
    (root / "samples" / "sample.py").write_text("must not read")
    inventory = repository_inventory(root, 100, code_only=True)
    assert [row["path"] for row in inventory] == ["orders.py"]
    monkeypatch.setattr("security_auditor.ingestion.extract_document", lambda *args: (_ for _ in ()).throw(AssertionError("document scanned")))

    class NoDocumentAgent:
        async def execute(self, *args):
            raise AssertionError("document or requirements agent ran")

    class NativeOnly:
        fingerprint = "test-native"

        def __init__(self):
            self.phases = []

        async def execute(self, run_id, phase, parent):
            self.phases.append(phase)

    native = NativeOnly()
    app = create_app(fixture_settings, executor=NoDocumentAgent(), mantis_executor=native)
    with TestClient(app) as client:
        pid = client.post("/api/v1/projects", json={"name": "code-only"}).json()["id"]
        assert client.post(f"/api/v1/projects/{pid}/runs", json={"mode": "code_only"}).status_code == 400
        client.post(f"/api/v1/projects/{pid}/documents", files={"file": ("design.md", b"must not read")})
        response = client.post(f"/api/v1/projects/{pid}/runs", json={"mode": "code_only", "repository_id": "fixture"})
        assert response.status_code == 202
        run = wait_run(client, pid, response.json()["id"])
        assert run["status"] == "COMPLETED", run
        assert run["snapshot"]["assets"] == [] and run["snapshot"]["standard"] is None
        assert run["snapshot"]["workflow_hash"] == digest(fixture_settings.workflow.read_bytes())
        assert native.phases == ["model", "audit"]
        sources = app.state.store.evidence(run["id"])
        assert sources and all(row["source_type"] == "code" for row in sources)
        assert {t["stage"] for t in app.state.store.rows("SELECT stage FROM tasks WHERE run_id=?", (run["id"],))} == {"ingestion", "threat_model", "vulnerability_research"}


def test_budget_pause_does_not_reset(fixture_settings, seed_run):
    from dataclasses import replace

    configured = replace(fixture_settings, max_requests=1)
    fixture_store = Store(configured.database, enforce_budgets=configured.enforce_budgets)
    app = create_app(configured, executor=FixtureExecutor(fixture_store),
                     mantis_executor=FixtureMantis(configured, fixture_store))
    with TestClient(app) as client:
        pid, rid = seed_run(client)
        result = wait_run(client, pid, rid)
        assert result["status"] == "PAUSED"
        assert result["usage"]["requests"] == 1
        assert client.post(f"/api/v1/projects/{pid}/runs/{rid}/resume").status_code == 400
        assert report(app.state.store, rid)["incomplete_tasks"]
        with app.state.store.connect() as db:
            db.execute("INSERT INTO run_budget_policy VALUES(?,1)", (rid,))
            db.execute("UPDATE runs SET max_requests=0,max_tokens=0 WHERE id=?", (rid,))
        resumed = client.post(f"/api/v1/projects/{pid}/runs/{rid}/resume")
        assert resumed.status_code == 202
        assert resumed.json()["usage"] == result["usage"]
        assert wait_run(client, pid, rid)["status"] == "COMPLETED"


def test_product_report_escapes_project_and_code():
    from security_auditor.product_report import product_report

    finding = {
        "title": "<b>漏洞</b>",
        "impact": "impact",
        "rationale": "reason",
        "recommendation": "fix",
        "evidence_ids": ["source"],
    }
    result = product_report(
        "<script>project</script>",
        {"run": {"demo": False}, "records": []},
        {"requirements": [], "findings": [finding]},
        [{"id": "source", "source_type": "code", "locator": "x.py:1", "content": "<script>code</script>"}],
    )
    assert "<script>" not in result
    assert "&lt;script&gt;code&lt;/script&gt;" in result
    assert "&lt;b&gt;漏洞&lt;/b&gt;" in result
    assert "Review result: Unconfirmed" in result
    for status, label in [("STATIC_SUPPORTED", "Supported by static review"), ("FALSE_POSITIVE", "Excluded")]:
        reviewed = product_report("project", {"records": []},
                                 {"requirements": [], "findings": [{**finding, "review_status": status}]}, [])
        assert f"Review result: {label}" in reviewed
        if status == "FALSE_POSITIVE":
            assert "No pending findings" in reviewed and "Excluded candidates (1)" in reviewed
    code_only = product_report("project", {"records": [], "run": {"mode": "code_only"}},
                               {"requirements": [], "findings": []}, [])
    assert code_only.count("Not assessed in this analysis") == 2


def test_clean_startup_has_no_seed_data_or_demo_route(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/projects").json()["items"] == []
        assert "/api/v1/demo" not in client.get("/openapi.json").json()["paths"]
        assert client.post("/api/v1/demo").status_code == 404
        assert client.get("/api/v1/projects").json()["items"] == []
