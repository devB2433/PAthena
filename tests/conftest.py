from pathlib import Path

import pytest

from security_auditor.config import ROOT, Settings
from security_auditor.store import Store


@pytest.fixture
def settings(tmp_path: Path):
    return Settings(
        state_dir=tmp_path / "state",
        skills_dir=ROOT / "skills",
        workflow=ROOT / "design/workflow.json",
        web_dist=tmp_path / "no-web",
        max_tokens=200000,
        max_requests=100,
        standard_pack="",  # Tests stay isolated from a user's locally imported standards.
    )


@pytest.fixture
def store(settings):
    return Store(settings.database)


@pytest.fixture
def sample_run(settings, store):
    project = store.create_project("test")
    run = store.create_run(project["id"], "full", {}, False, 100, 200000)
    eid = store.add_evidence(run["id"], "code", "orders.py:1", "return db.query_all()", {})
    return project, run, eid


@pytest.fixture
def fixture_settings(settings):
    from dataclasses import replace

    repository = settings.state_dir.parent / "test-repository"
    repository.mkdir()
    (repository / "orders.py").write_text(
        "def get_order(db, tenant, order_id):\n    return db.query(tenant=tenant, id=order_id)\n\n"
        "def export_orders(db, tenant):\n    return db.query_all()\n"
    )
    return replace(settings, repositories={"fixture": str(repository)})


@pytest.fixture
def seed_run():
    def create(client):
        project = client.post("/api/v1/projects", json={"name": "测试项目"}).json()
        pid = project["id"]
        response = client.post(
            f"/api/v1/projects/{pid}/documents",
            files={"file": ("design.md", "读取和导出订单必须校验租户范围。".encode())},
        )
        assert response.status_code == 201
        response = client.post(
            f"/api/v1/projects/{pid}/runs", json={"mode": "full", "repository_id": "fixture"}
        )
        assert response.status_code == 202
        return pid, response.json()["id"]

    return create
