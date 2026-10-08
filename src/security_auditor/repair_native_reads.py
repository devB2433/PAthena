"""Recover legacy read receipts from actual, source-matched native tool responses.

Administrative repair only: does not execute a model or the target program.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from .config import Settings, contained_file
from .domain import StageOutput
from .mantis_engine import MantisEngine
from .mantis_paths import native_source_path
from .mantis_worker import native_path, write_json
from .native_progress import reconcile_native_progress
from .skills import digest
from .store import Store


def recover_reads(work: Path, phase: str, manifest: dict) -> list[dict]:
    native_path()
    from core.llm_gateway import UNTRUSTED_DATA_START, wrap_untrusted_content
    from tools.research_tools import MAX_READ_SIZE

    allowed = {f["path"]: f["sha256"] for f in manifest["files"] if f["status"] == "READABLE"}
    calls, reads = {}, []
    root = work / "snapshot"
    with sqlite3.connect(work / f"{phase}-sessions.db") as db:
        events = db.execute("SELECT session_id,event_data FROM events ORDER BY timestamp")
        for session, encoded in events:
            event = json.loads(encoded)
            for part in (event.get("content") or {}).get("parts", []):
                call = part.get("function_call")
                if call and call.get("name") == "read_file" and call.get("id"):
                    calls[(session, call["id"])] = call.get("args") or {}
                response = part.get("function_response")
                if not response or response.get("name") != "read_file":
                    continue
                args = calls.get((session, response.get("id")))
                if not args or (event.get("content") or {}).get("role") != "user":
                    continue
                text = (response.get("response") or {}).get("result", "")
                if not isinstance(text, str) or UNTRUSTED_DATA_START not in text:
                    continue
                try:
                    path = native_source_path(root, args["filepath"])
                    source = contained_file(root, path).read_bytes()
                    if path not in allowed or digest(source) != allowed[path]:
                        continue
                    # Match both the returned filename and exact scrubbed source
                    # wrapper. A successful function call alone is insufficient.
                    header = re.search(re.escape(UNTRUSTED_DATA_START) + r" \(file: ([^\n]*)\)\n", text)
                    if not header or native_source_path(root, header[1]) != path:
                        continue
                    raw = source.decode("utf-8", errors="replace")
                    lines = raw.splitlines(keepends=True)
                    start, end = int(args.get("start_line", 0)), int(args.get("end_line", 0))
                    lo, hi = max(1, start), min(end if end > 0 else len(lines), len(lines))
                    selected = "".join(lines[lo - 1:hi]) if start > 0 or end > 0 else raw
                    returned = selected
                    if len(returned) > MAX_READ_SIZE:
                        returned = returned[:MAX_READ_SIZE] + f"\n\n[TRUNCATED: File exceeds {MAX_READ_SIZE} characters/bytes limit]"
                        hi = lo + selected[:MAX_READ_SIZE].count("\n") - 1
                    if hi < lo or wrap_untrusted_content(returned, filename=header[1]) not in text:
                        continue
                    receipt = {"path": path, "start": lo, "end": hi, "stage": event.get("author")}
                    if receipt not in reads:
                        reads.append(receipt)
                except (KeyError, TypeError, ValueError, OSError):
                    continue
    return reads


def repair(settings: Settings, run_id: str) -> dict:
    store = Store(settings.database, enforce_budgets=settings.enforce_budgets)
    run = store.run(run_id)
    if run["status"] != "COMPLETED":
        raise ValueError("仅修复已完成运行，避免修改活动分析")
    engine = MantisEngine(settings, store)
    work = engine.workspace(run_id)
    counts = {}
    for phase in ("model", "audit"):
        path = work / f"{phase}-output.json"
        result = json.loads(path.read_text())
        snapshot_id = digest(json.dumps(run["snapshot"], sort_keys=True).encode())
        if result.get("error") or result.get("phase") != phase or result.get("snapshot_id") != snapshot_id:
            raise ValueError("原生结果与运行快照不一致")
        reads = recover_reads(work, phase, run["snapshot"]["repository"])
        for read in result["reads"]:
            canonical = {**read, "path": native_source_path(work / "snapshot", read["path"])}
            if canonical not in reads:
                reads.append(canonical)
        original = work / f"{phase}-output.before-read-repair.json"
        if not original.exists():
            original.write_bytes(path.read_bytes())
            original.chmod(0o444)
        result["reads"] = reads
        write_json(path, result)
        parent = store.rows("SELECT id FROM tasks WHERE run_id=? AND stage=? AND wave='engine'",
                            (run_id, "mantis_" + phase))[0]["id"]
        task = store.add_task(run_id, "mantis_receipt_repair", {"phase": phase, "mapping_hash": engine.fingerprint},
                              parent, "engine")
        store.start_task(task, instruction_hash=engine.fingerprint)
        output = engine.map_output(run_id, task, result)
        known = {r["id"] for r in store.records(run_id)}
        fresh = [r for r in output.records if r.id not in known]
        store.commit_output(task, "mantis_engine", StageOutput(records=fresh, gaps=output.gaps,
                            summary="从实际原生工具响应恢复源码引用；既有模型判断和用量保留"))
        prior = store.task(parent)["result"] or {}
        if reads and any("未记录可验证的源码读取" in g for g in prior.get("gaps", [])):
            store.event(run_id, "native_mapping_gap_repaired", {"task_id": parent, "original_result": prior,
                                                                "repair_task_id": task})
            store.finish_service(parent, {**prior, "read_receipt_repair_task": task,
                "gaps": [g for g in prior.get("gaps", []) if "未记录可验证的源码读取" not in g]})
        reconcile_native_progress(store, run_id, work, phase)
        store.event(run_id, "native_read_receipts_repaired", {"phase": phase, "receipts": len(reads),
                    "mapped_records": len(fresh), "original_analysis_hash": run["snapshot"]["mantis_hash"],
                    "mapping_hash": engine.fingerprint, "original_export": original.name})
        counts[phase] = {"receipts": len(reads), "mapped_records": len(fresh)}
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+")
    args = parser.parse_args()
    settings = Settings()
    for run_id in args.run_ids:
        print(json.dumps({"run_id": run_id, "repaired": repair(settings, run_id)}, ensure_ascii=False))
