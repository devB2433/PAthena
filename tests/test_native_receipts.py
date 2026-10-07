import json
import sqlite3

import pytest

from security_auditor.mantis_paths import native_source_path
from security_auditor.mantis_worker import native_path
from security_auditor.repair_native_reads import recover_reads
from security_auditor.skills import digest


def test_completed_model_receipts_are_read_from_database_without_inference(store, sample_run):
    from security_auditor.runtime import report, result_matrix
    from security_auditor.product_report import product_report
    project, run, _ = sample_run
    parent = store.add_task(run['id'], 'mantis_model', {})
    child = store.add_task(run['id'], 'mantis_threat_modeler', {}, parent)
    data = {'threat_actors': ['reader'], 'trust_boundaries': ['browser/app'],
            'entry_points': ['export'], 'key_risks': ['Unbounded export'], 'threats': []}
    store.save_model_output(child, json.dumps({'content': {'text': 'There is probably a vulnerability'}}))
    store.save_model_submission(child, 'threat_modeler', 'record_threat_model', 'source', data)
    store.finish_service(child, {})
    assert store.model_artifacts(run['id']) == []
    store.finish_service(parent, {})
    expected = [{'artifact_type': 'threat_model', 'data': data}]
    assert store.model_artifacts(run['id']) == expected
    output = report(store, run['id'])
    matrix = result_matrix(store, run['id'])
    assert output['model_artifacts'] == matrix['model_artifacts'] == expected
    assert not output['records']  # No synthetic threats or model interpretation.
    html = product_report(project['name'], output, matrix, [])
    assert 'Key risks' in html and 'Unbounded export' in html
    assert 'There is probably' not in html


def test_model_receipt_archive_hash_is_checked_before_display(store, sample_run):
    _, run, _ = sample_run
    parent = store.add_task(run['id'], 'mantis_model', {})
    child = store.add_task(run['id'], 'mantis_threat_modeler', {}, parent)
    store.save_model_submission(child, 'threat_modeler', 'record_threat_model', 'source', {'key_risks': ['risk']})
    store.finish_service(parent, {})
    store.finish_service(child, {})
    with store.connect() as db:
        db.execute("UPDATE stage_artifacts SET payload='tampered' WHERE task_id=?", (child,))
    with pytest.raises(ValueError, match='归档摘要'):
        store.model_artifacts(run['id'])


def test_absolute_native_paths_stay_inside_source_jail(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    source = root / "safe.py"
    source.write_text("pass\n")
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n")
    (root / "alias.py").symlink_to(outside)
    assert native_source_path(root, str(source)) == "safe.py"
    assert native_source_path(root, "./safe.py") == "safe.py"
    for path in [str(outside), "../outside.py", str(root / "../outside.py"), "alias.py"]:
        with pytest.raises(ValueError):
            native_source_path(root, path)


def test_recovery_requires_actual_matching_response_and_frozen_source(tmp_path):
    native_path()
    from core.llm_gateway import wrap_untrusted_content
    root = tmp_path / "snapshot"
    root.mkdir()
    source = root / "safe.py"
    raw = "def f():\n    return 1\n"
    source.write_text(raw)
    manifest = {"files": [{"path": "safe.py", "status": "READABLE", "sha256": digest(raw.encode())}]}
    with sqlite3.connect(tmp_path / "model-sessions.db") as db:
        db.execute("CREATE TABLE events(session_id TEXT,event_data TEXT,timestamp INTEGER)")
        call = {"content": {"role": "model", "parts": [{"function_call": {
            "name": "read_file", "id": "real", "args": {"filepath": str(source)}}}]}}
        response = {"author": "architect", "content": {"role": "user", "parts": [{"function_response": {
            "name": "read_file", "id": "real", "response": {"result": "Error: Permission denied"}}}]}}
        db.execute("INSERT INTO events VALUES('session',?,1)", (json.dumps(call),))
        db.execute("INSERT INTO events VALUES('session',?,2)", (json.dumps(response),))
    assert recover_reads(tmp_path, "model", manifest) == []
    with sqlite3.connect(tmp_path / "model-sessions.db") as db:
        response["content"]["parts"][0]["function_response"]["response"]["result"] = wrap_untrusted_content(raw, filename=str(source))
        db.execute("UPDATE events SET event_data=? WHERE timestamp=2", (json.dumps(response),))
    assert recover_reads(tmp_path, "model", manifest) == [{"path": "safe.py", "start": 1, "end": 2, "stage": "architect"}]
    source.write_text("changed\n")
    assert recover_reads(tmp_path, "model", manifest) == []
