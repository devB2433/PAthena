import json
import sqlite3

from security_auditor.native_progress import reconcile_native_progress
from security_auditor.skills import digest


def test_resume_keeps_usage_and_only_closes_proven_campaign_tasks(store, sample_run, tmp_path):
    _, run, _ = sample_run
    first = store.add_task(run["id"], "mantis_researcher", {"phase": "audit", "campaign": "/snapshot/a"}, wave="native")
    other = store.add_task(run["id"], "mantis_researcher", {"phase": "audit", "campaign": "/snapshot/b"}, wave="native")
    critic = store.add_task(run["id"], "mantis_critic", {"phase": "audit", "campaign": "/snapshot/a"}, wave="native")
    store.fail_task(first, "interrupted", "PENDING")
    store.settle(store.reserve(run["id"], first, 1000), 900)
    original_usage = store.run(run["id"])["usage"]
    result = {"phase": "audit", "snapshot_id": digest(json.dumps(run["snapshot"], sort_keys=True).encode()),
              "verdicts": [{"stage": "critic", "verdict": {"route": "non_viable", "reason": "Fallback: invalid output"}}]}
    (tmp_path / "audit-output.json").write_text(json.dumps(result))
    with sqlite3.connect(tmp_path / "audit-sessions.db") as db:
        db.execute("CREATE TABLE events(session_id TEXT,event_data TEXT)")
        session = f"session_run_{run['id']}_{digest(b'/snapshot/a')[:8]}"
        for stage in ["researcher", "critic"]:
            db.execute("INSERT INTO events VALUES(?,?)", (session, json.dumps({
                "author": stage, "node_info": {"message_as_output": True}})))
    reconcile_native_progress(store, run["id"], tmp_path, "audit")
    assert store.task(first)["status"] == "SUCCEEDED" and store.task(first)["error"] is None
    assert store.task(other)["status"] == "PENDING"
    assert store.run(run["id"])["usage"] == original_usage
    assert "结构化结论未生成" in store.task(critic)["result"]["gaps"][0]
    assert store.records(run["id"], "finding") == []


def test_failed_or_foreign_export_cannot_close_tasks(store, sample_run, tmp_path):
    _, run, _ = sample_run
    tid = store.add_task(run["id"], "mantis_researcher", {"phase": "audit", "campaign": "/snapshot"}, wave="native")
    with sqlite3.connect(tmp_path / "audit-sessions.db") as db:
        db.execute("CREATE TABLE events(session_id TEXT,event_data TEXT)")
    for result in [{"error": "budget"}, {"phase": "audit", "snapshot_id": "other-snapshot"}]:
        (tmp_path / "audit-output.json").write_text(json.dumps(result))
        reconcile_native_progress(store, run["id"], tmp_path, "audit")
        assert store.task(tid)["status"] == "PENDING"
