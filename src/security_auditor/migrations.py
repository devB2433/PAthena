"""Versioned, transactional upgrades; archived model text is never inferred."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .skills import digest

VERSION = 3
SUBMISSIONS = {"record_summary": ("architect", "summary"),
               "record_threat_model": ("threat_modeler", "threat_model"),
               "record_plan": ("planner", "plan"),
               "report_findings": (None, "report")}


def submission(content: str, task_stage: str):
    try:
        value = json.loads(content)
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != {'stage', 'campaign', 'tool', 'parameters'}:
        return None
    if not all(isinstance(value[k], str) for k in ('stage', 'campaign', 'tool')):
        return None
    spec = SUBMISSIONS.get(value['tool'])
    if not spec or task_stage != 'mantis_' + value['stage'] or not isinstance(value['parameters'], dict):
        return None
    if spec[0] is not None and spec[0] != value['stage']:
        return None
    return spec[1], value


def insert_artifact(db, output_id: str, task_id: str, run_id: str, task_stage: str,
                    content: str, created: str):
    parsed = submission(content, task_stage)
    if not parsed:
        return
    kind, value = parsed
    payload = json.dumps(value['parameters'], ensure_ascii=False, sort_keys=True)
    db.execute("INSERT OR IGNORE INTO stage_artifacts VALUES(?,?,?,?,?,?,?,?,?,?)", (
        output_id, run_id, task_id, value['stage'], kind, value['campaign'],
        payload, digest(payload.encode()), output_id, created))


def migrate(path: Path):
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
        current = db.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0]
    if current > VERSION:
        raise ValueError('数据库版本高于当前程序，请使用对应程序版本')
    if current == VERSION:
        return
    # A consistent SQLite backup includes WAL data; copying only the file does not.
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        if db.execute('SELECT count(*) FROM projects').fetchone()[0]:
            backups = path.parent / 'backups'
            backups.mkdir(exist_ok=True)
            backup = backups / (path.stem + f'-before-v{VERSION}-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.sqlite3')
            with closing(sqlite3.connect(backup)) as target, target:
                db.backup(target)
            backup.chmod(0o600)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0] == VERSION:
            return
        current = db.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0]
        if current < 1:
            db.execute('CREATE TABLE stage_artifacts(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),'
                       'task_id TEXT NOT NULL REFERENCES tasks(id),stage TEXT NOT NULL,artifact_type TEXT NOT NULL,'
                       'campaign TEXT NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL,'
                       'submission_output_id TEXT NOT NULL UNIQUE REFERENCES model_outputs(id),created TEXT NOT NULL)')
            db.execute('CREATE INDEX idx_artifacts_run ON stage_artifacts(run_id,artifact_type,created)')
            db.execute('CREATE TABLE run_issues(run_id TEXT PRIMARY KEY REFERENCES runs(id),code TEXT NOT NULL,'
                       'message TEXT NOT NULL,updated TEXT NOT NULL)')
            for row in db.execute('SELECT m.*,t.run_id,t.stage FROM model_outputs m JOIN tasks t ON t.id=m.task_id '
                                  "WHERE t.stage LIKE 'mantis_%' ORDER BY m.created,m.id"):
                if digest(row['content'].encode()) != row['sha256']:
                    raise ValueError('模型产物归档摘要不一致，数据库升级已回滚')
                insert_artifact(db, row['id'], row['task_id'], row['run_id'], row['stage'], row['content'], row['created'])
            db.execute("INSERT INTO run_issues SELECT id,'analysis_incomplete',error,? FROM runs "
                       "WHERE error IS NOT NULL AND status IN ('PAUSED','FAILED','WAITING')", (datetime.now(timezone.utc).isoformat(),))
            db.execute('INSERT INTO schema_migrations VALUES(1,?)', (datetime.now(timezone.utc).isoformat(),))
        if current < 2:
            db.execute('CREATE TABLE system_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('INSERT INTO schema_migrations VALUES(2,?)', (datetime.now(timezone.utc).isoformat(),))
        if current < 3:
            db.execute('CREATE TABLE IF NOT EXISTS standard_versions(id TEXT PRIMARY KEY,standard_id TEXT NOT NULL,'
                       'version TEXT NOT NULL,manifest TEXT NOT NULL,clause_count INTEGER NOT NULL,context_count INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS standard_clauses(pack_id TEXT NOT NULL REFERENCES standard_versions(id),'
                       'clause_id TEXT NOT NULL,parent_id TEXT NOT NULL,defined_approach TEXT NOT NULL,'
                       'applicability_notes TEXT NOT NULL,testing_procedures TEXT NOT NULL,guidance TEXT NOT NULL,'
                       'payload TEXT NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(pack_id,clause_id))')
            db.execute('CREATE TABLE IF NOT EXISTS standard_contexts(pack_id TEXT NOT NULL REFERENCES standard_versions(id),'
                       'context_id TEXT NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(pack_id,context_id))')
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS standard_clause_search USING fts5(pack_id UNINDEXED,'
                       'clause_id UNINDEXED,content)')
            db.execute('INSERT INTO schema_migrations VALUES(3,?)', (datetime.now(timezone.utc).isoformat(),))
