"""Reconcile resumed native tasks against retained ADK completion events."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .skills import digest
from .store import Store


def reconcile_native_progress(store: Store, run_id: str, work: Path, phase: str) -> None:
    output, sessions = work / f"{phase}-output.json", work / f"{phase}-sessions.db"
    if not output.is_file() or not sessions.is_file():
        return
    result = json.loads(output.read_text())
    snapshot_id = digest(json.dumps(store.run(run_id)["snapshot"], sort_keys=True).encode())
    if result.get("error") or result.get("phase") != phase or result.get("snapshot_id") != snapshot_id:
        return
    done = set()
    with sqlite3.connect(sessions) as db:
        for session_id, encoded in db.execute("SELECT session_id,event_data FROM events"):
            event = json.loads(encoded)
            if (event.get("node_info") or {}).get("message_as_output") and not event.get("error_code"):
                done.add((session_id, event.get("author")))
    tasks = store.rows("SELECT * FROM tasks WHERE run_id=? AND wave='native'", (run_id,))
    tasks = [t for t in tasks if json.loads(t["scope"]).get("phase") == phase]
    campaigns = {json.loads(t["scope"]).get("campaign", "") for t in tasks}
    verdicts = {v["stage"]: v["verdict"] for v in result.get("verdicts", [])}
    for task in tasks:
        scope = json.loads(task["scope"])
        stage = task["stage"].removeprefix("mantis_")
        campaign = str(scope.get("campaign", "")).replace("\n", "").replace("\r", "").strip()
        session_id = f"session_run_{run_id}_{digest(campaign.encode())[:8]}"
        completed = (session_id, stage) in done
        # The native calibrator is a function node, not an ADK agent. Its
        # successful per-finding persisted scores prove completion only when
        # there is a single campaign; do not infer another slice's completion.
        if stage == "calibrator" and len(campaigns) == 1:
            findings = result.get("findings", [])
            completed = bool(findings) and all(f.get("mantis_risk_score") is not None for f in findings)
        if not completed:
            continue
        gaps = []
        if str(verdicts.get(stage, {}).get("reason", "")).startswith("Fallback:"):
            gaps.append(f"Mantis {stage} 结构化结论未生成，保留待确认状态；回退不代表排除漏洞")
        if task["status"] in {"PENDING", "RUNNING"} or gaps:
            store.finish_service(task["id"], {"engine": "mantis", "gaps": gaps,
                                             "completion_source": sessions.name})
            store.event(run_id, "native_progress_reconciled", {"task_id": task["id"], "stage": stage,
                                                               "gaps": gaps})
