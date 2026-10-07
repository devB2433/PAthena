from dataclasses import replace

import pytest

from security_auditor.domain import Fact, StageOutput
from security_auditor.runtime import Scheduler
from security_auditor.provider import OutputContractError


@pytest.mark.asyncio
async def test_invalid_output_is_saved_and_never_sent_for_model_repair(settings, store, sample_run):
    _, run, eid = sample_run

    class InvalidExecutor:
        calls = 0
        async def execute(self, task, bundle, context):
            self.calls += 1
            assert "repair_feedback" not in context
            return StageOutput(records=[Fact(title="Bad citation", module="orders", fact_type="ENTRYPOINT",
                                basis="OBSERVED", evidence_ids=[eid], rationale="Unread source")], summary="Invalid citation")

    executor = InvalidExecutor()
    scheduler = Scheduler(settings, store, executor)
    parent = store.add_task(run["id"], "architecture", {}, wave="stage")
    node = {"id": "architecture", "role": "code_architect", "skill_id": "code_architect", "skill_version": "0.1.0"}
    with pytest.raises(OutputContractError) as error:
        await scheduler.child(run["id"], node, parent, {"evidence_ids": [eid]})
    assert error.value.code == 'output'
    assert executor.calls == 1
    assert not store.records(run["id"])
    assert store.rows("SELECT content FROM model_outputs")
    assert not store.rows("SELECT id FROM events WHERE event_type='output_repair'")
    assert store.rows("SELECT status FROM tasks WHERE parent_task_id=?", (parent,))[0]["status"] == "FAILED"


def test_invalid_operating_limits_fail_at_startup(settings):
    for options in [{"concurrency": 0}, {"max_requests": -1}, {"max_tokens": -1}]:
        with pytest.raises(ValueError):
            replace(settings, **options)
