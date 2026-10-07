"""Durable accounting at the outbound boundary; no model interpretation here."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(Exception):
    pass


class ProviderUnavailable(Exception):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code or 'unavailable'


class ProviderBlocked(Exception):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code or 'authentication'


class OutputContractError(ValueError):
    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code or 'output'


MESSAGES = {
    "transport": "模型连接中断或超时，等待自动恢复",
    "rate_limit": "模型服务限流，等待自动恢复",
    "unavailable": "模型服务暂时不可用，等待自动恢复",
    "authentication": "模型密钥无效，请修改模型配置后继续",
    "balance": "模型账户余额不足，充值后可继续",
    "parameters": "模型请求参数不符合接口要求",
    "output": "模型结果不符合结构化契约，原始返回已保存，阶段未完成",
    "input_context": "分析任务输入过大，需要精简上下文；尚未调用模型",
    "budget": "累计模型预算已用尽，进度已保存",
    "pause": "用户请求暂停，进度已保存",
}


def category(status: int) -> str:
    if status in (401, 403):
        return "authentication"
    if status == 402:
        return "balance"
    if status == 429:
        return "rate_limit"
    if status == 408 or status >= 500:
        return "unavailable"
    return "parameters"


def raise_failure(code: str):
    message = MESSAGES.get(code, MESSAGES["output"])
    if code in {"transport", "rate_limit", "unavailable"}:
        raise ProviderUnavailable(message, code=code)
    if code in {"authentication", "balance"}:
        raise ProviderBlocked(message, code=code)
    if code in {"budget", "pause"}:
        raise BudgetExceeded(message)
    raise OutputContractError(message, code=code)


def estimate_tokens(body: dict) -> int:
    # Explicit estimate, never presented as supplier usage. Includes tools and
    # the FINAL gateway output allowance, not the adapter's old 4096 constant.
    prompt = json.dumps({k: body.get(k) for k in ("messages", "tools", "response_format")}, ensure_ascii=False)
    return (len(prompt.encode()) + 2) // 3 + int(body.get("max_tokens") or 4096)


def parse_model_json(text: str):
    """Decode JSON, allowing only literal escaped whitespace outside strings.

    Preserve every string, key, value and delimiter. Never fill missing fields,
    commas or brackets. Return the exact protocol projection for audit.
    """
    try:
        return json.loads(text), text
    except json.JSONDecodeError:
        parts, quoted, escaped, i = [], False, False, 0
        while i < len(text):
            char = text[i]
            if quoted:
                parts.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
                parts.append(char)
            elif char == "\\" and i + 1 < len(text) and text[i + 1] in "nrt":
                parts.append({"n": "\n", "r": "\r", "t": "\t"}[text[i + 1]])
                i += 1
            else:
                parts.append(char)
            i += 1
        projected = "".join(parts)
        return json.loads(projected), projected


class ProviderLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS run_controls(run_id TEXT PRIMARY KEY,
                max_requests INTEGER,max_tokens INTEGER,physical INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS provider_requests(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,logical_id TEXT NOT NULL,attempt INTEGER NOT NULL,
                request_hash TEXT NOT NULL,reserved_tokens INTEGER NOT NULL,actual_tokens INTEGER,
                status TEXT NOT NULL,category TEXT,http_status INTEGER,response TEXT,created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_provider_run ON provider_requests(run_id);
            CREATE INDEX IF NOT EXISTS idx_provider_logical ON provider_requests(logical_id,created);
            CREATE TABLE IF NOT EXISTS run_recovery(run_id TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL DEFAULT 0,wake_at REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS provider_faults(logical_id TEXT PRIMARY KEY,category TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_output_transforms(request_id TEXT PRIMARY KEY,
                fields TEXT NOT NULL,rule TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_protocols(request_id TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,tools TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def configure(self, run_id: str, requests: int | None, tokens: int | None):
        if (requests is not None and requests < 1) or (tokens is not None and tokens < 1):
            raise ValueError("预算必须为正数或明确不限额")
        with self.connect() as db:
            db.execute("INSERT INTO run_controls VALUES(?,?,?,1) ON CONFLICT(run_id) DO UPDATE SET "
                       "max_requests=excluded.max_requests,max_tokens=excluded.max_tokens",
                       (run_id, requests, tokens))

    def note_protocol(self, request_id: str, endpoint: str, tools: list):
        # Archive only tool declarations, never credentials or conversation/source text.
        with self.connect() as db:
            db.execute('INSERT INTO provider_protocols VALUES(?,?,?)',
                       (request_id, endpoint, json.dumps(tools, ensure_ascii=False)))

    def policy(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM run_controls WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def usage(self, run_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT count(*) requests,COALESCE(SUM(actual_tokens),0) known_tokens,"
                             "COALESCE(SUM(CASE WHEN status='IN_FLIGHT' THEN reserved_tokens ELSE 0 END),0) reserved_tokens,"
                             "COALESCE(SUM(CASE WHEN status='UNKNOWN' THEN reserved_tokens ELSE 0 END),0) unknown_tokens,"
                             "SUM(CASE WHEN status='UNKNOWN' THEN 1 ELSE 0 END) unknown_requests "
                             "FROM provider_requests WHERE run_id=?", (run_id,)).fetchone()
        result = dict(row)
        result["unknown_requests"] = result["unknown_requests"] or 0
        result["tokens"] = result["known_tokens"] + result["reserved_tokens"] + result["unknown_tokens"]
        return result

    def begin(self, run_id: str, task_id: str, logical_id: str, attempt: int, body: dict) -> str:
        reserved = estimate_tokens(body)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT status,pause_requested FROM runs WHERE id=?", (run_id,)).fetchone()
            task = db.execute("SELECT run_id FROM tasks WHERE id=?", (task_id,)).fetchone()
            policy = db.execute("SELECT * FROM run_controls WHERE run_id=?", (run_id,)).fetchone()
            if not run or not task or task[0] != run_id or not policy:
                raise ValueError("请求不属于已登记的分析任务")
            if run["pause_requested"] or run["status"] in {"PAUSED", "FAILED", "COMPLETED"}:
                raise BudgetExceeded(MESSAGES["pause"])
            used = db.execute("SELECT count(*),COALESCE(SUM(COALESCE(actual_tokens,"
                              "CASE WHEN status IN ('UNKNOWN','IN_FLIGHT') THEN reserved_tokens ELSE 0 END)),0) "
                              "FROM provider_requests WHERE run_id=?", (run_id,)).fetchone()
            if ((policy["max_requests"] is not None and used[0] >= policy["max_requests"]) or
                    (policy["max_tokens"] is not None and used[1] + reserved > policy["max_tokens"])):
                raise BudgetExceeded(MESSAGES["budget"])
            rid = str(uuid.uuid4())
            db.execute("INSERT INTO provider_requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                rid, run_id, task_id, logical_id, attempt,
                hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(), reserved, None,
                "IN_FLIGHT", None, None, None, datetime.now(timezone.utc).isoformat()))
        return rid

    def fault(self, logical_id: str, code: str):
        with self.connect() as db:
            db.execute("INSERT INTO provider_faults VALUES(?,?) ON CONFLICT(logical_id) DO UPDATE SET category=excluded.category",
                       (logical_id, code))

    def note_transform(self, rid: str, fields: list[str]):
        with self.connect() as db:
            db.execute("INSERT INTO provider_output_transforms VALUES(?,?,?)",
                       (rid, json.dumps(fields), "escaped_whitespace_outside_strings_v1"))

    def finish(self, rid: str, code: str | None, http_status: int | None, response: str | None = None,
               tokens: int | None = None, definitely_unbilled: bool = False):
        if tokens is not None and (not isinstance(tokens, int) or tokens < 0):
            tokens = None
        state = "KNOWN" if tokens is not None else "REJECTED" if definitely_unbilled else "UNKNOWN"
        with self.connect() as db:
            row = db.execute("SELECT logical_id,status,run_id FROM provider_requests WHERE id=?", (rid,)).fetchone()
            if not row or (row["status"] != "IN_FLIGHT" and not (row["status"] == "UNKNOWN" and (tokens is not None or response is not None))):
                return
            db.execute("UPDATE provider_requests SET status=?,category=?,http_status=?,response=?,actual_tokens=? "
                       "WHERE id=?", (state, code, http_status, response, tokens, rid))
            db.execute("DELETE FROM provider_faults WHERE logical_id=?", (row["logical_id"],))
            if code:
                db.execute("INSERT INTO provider_faults VALUES(?,?)", (row["logical_id"], code))
            elif http_status == 200:
                db.execute('DELETE FROM run_recovery WHERE run_id=?', (row['run_id'],))

    def failure(self, logical_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT category FROM provider_faults WHERE logical_id=?",
                             (logical_id,)).fetchone()
        return row[0] if row else None

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE provider_requests SET status='UNKNOWN',category='transport' WHERE status='IN_FLIGHT'")
