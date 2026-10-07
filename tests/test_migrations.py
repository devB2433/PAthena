import json
import sqlite3

import pytest

from security_auditor.store import Store


def legacy_receipt(store, sample_run):
    _, run, _ = sample_run
    parent = store.add_task(run['id'], 'mantis_model', {})
    child = store.add_task(run['id'], 'mantis_threat_modeler', {}, parent)
    data = {'key_risks': ['original risk'], 'threats': []}
    receipt = store.save_model_output(child, json.dumps({
        'stage': 'threat_modeler', 'tool': 'record_threat_model',
        'campaign': 'source', 'parameters': data}))
    store.save_model_output(child, 'Unstructured opinion; must not become a result')
    store.finish_service(child, {})
    store.finish_service(parent, {})
    store.settle(store.reserve(run['id'], child, 100), 50)
    with store.connect() as db:
        db.execute('DROP TABLE stage_artifacts')
        db.execute('DROP TABLE run_issues')
        db.execute('DROP TABLE system_settings')
        db.execute('DELETE FROM schema_migrations')
    return run, receipt, data


def test_upgrade_backfills_only_receipts_preserves_usage_and_is_idempotent(store, sample_run):
    run, receipt, data = legacy_receipt(store, sample_run)
    before = store.rows('SELECT * FROM usage')
    upgraded = Store(store.path)
    artifacts = upgraded.rows('SELECT * FROM stage_artifacts')
    assert len(artifacts) == 1 and artifacts[0]['id'] == receipt
    assert upgraded.model_artifacts(run['id']) == [{'artifact_type': 'threat_model', 'data': data}]
    assert upgraded.rows('SELECT * FROM usage') == before
    backups = list((store.path.parent / 'backups').glob('*before-v*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute('SELECT count(*) FROM model_outputs').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 0
    Store(store.path)
    assert len(list((store.path.parent / 'backups').glob('*before-v*.sqlite3'))) == 1
    assert upgraded.rows('PRAGMA foreign_key_check') == []


def test_corrupt_legacy_receipt_rolls_back_upgrade(store, sample_run):
    _, receipt, _ = legacy_receipt(store, sample_run)
    with store.connect() as db:
        db.execute("UPDATE model_outputs SET content='tampered' WHERE id=?", (receipt,))
    with pytest.raises(ValueError, match='回滚'):
        Store(store.path)
    assert store.rows('SELECT * FROM schema_migrations') == []
    assert not store.rows("SELECT name FROM sqlite_master WHERE name='stage_artifacts'")


def test_raw_archive_is_not_a_product_read_path(store, sample_run):
    _, run, _ = sample_run
    parent = store.add_task(run['id'], 'mantis_model', {})
    child = store.add_task(run['id'], 'mantis_threat_modeler', {}, parent)
    data = {'key_risks': ['original']}
    rid = store.save_model_submission(child, 'threat_modeler', 'record_threat_model', 'source', data)
    store.finish_service(child, {})
    store.finish_service(parent, {})
    with store.connect() as db:
        db.execute("UPDATE model_outputs SET content='not JSON' WHERE id=?", (rid,))
    assert store.model_artifacts(run['id'])[0]['data'] == data


def test_wrong_submission_role_cannot_write_partial_artifact(store, sample_run):
    _, run, _ = sample_run
    child = store.add_task(run['id'], 'mantis_researcher', {})
    with pytest.raises(ValueError, match='角色'):
        store.save_model_submission(child, 'researcher', 'record_threat_model', 'source', {})
    assert store.rows('SELECT * FROM model_outputs') == []
    assert store.rows('SELECT * FROM stage_artifacts') == []


def test_storage_failure_cannot_leave_half_a_submission(store, sample_run, monkeypatch):
    _, run, _ = sample_run
    child = store.add_task(run['id'], 'mantis_threat_modeler', {})
    def unavailable(*args):
        raise sqlite3.OperationalError('fixture storage failure')
    monkeypatch.setattr('security_auditor.store.insert_artifact', unavailable)
    with pytest.raises(sqlite3.OperationalError):
        store.save_model_submission(child, 'threat_modeler', 'record_threat_model', 'source', {})
    assert store.rows('SELECT * FROM model_outputs') == []
    assert store.rows('SELECT * FROM stage_artifacts') == []
