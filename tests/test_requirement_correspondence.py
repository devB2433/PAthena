import pytest

from security_auditor.domain import Assessment, Finding, Requirement, StageOutput
from security_auditor.product_report import product_report
from security_auditor.runtime import result_matrix


def baseline(store, run_id, eid, title="访问控制"):
    req = Requirement(title=title, module="订单", statement=title,
                      acceptance_criteria=["单条查询", "批量查询"], origin="EXPLICIT_DESIGN",
                      evidence_ids=[eid], rationale="设计要求")
    tid = store.add_task(run_id, "requirements", {"title": title})
    store.start_task(tid)
    store.note_read(tid, eid)
    store.commit_output(tid, "requirement_generator", StageOutput(records=[req], summary="需求"))
    return req


def check_task(store, run_id, req, eid, attempt=0):
    parent = store.add_task(run_id, "requirement_check", {"stage": "requirement_check"}, wave="stage")
    tid = store.add_task(run_id, "requirement_check", {"requirement_id": req.id,
                        "acceptance_criteria": req.acceptance_criteria, "attempt": attempt}, parent)
    store.start_task(tid)
    store.note_read(tid, eid)
    return tid


def assessments(req, eid, states):
    return [Assessment(title=criterion, requirement_id=req.id, acceptance_criterion=criterion,
                       module="订单", entrypoint="/orders", design_status="SUPPORTED",
                       implementation_status=status, evidence_ids=[eid], rationale="静态检查")
            for criterion, status in zip(req.acceptance_criteria, states)]


def test_same_named_criteria_cannot_be_assigned_to_another_requirement(store, sample_run):
    _, run, eid = sample_run
    first = baseline(store, run["id"], eid)
    second = baseline(store, run["id"], eid, "租户隔离")
    tid = check_task(store, run["id"], first, eid)
    with pytest.raises(ValueError, match="当前需求"):
        store.commit_output(tid, "requirement_checker", StageOutput(
            records=assessments(second, eid, ["STATIC_SUPPORTED", "STATIC_SUPPORTED"]), summary="错配"))
    assert not store.records(run["id"], "assessment")


@pytest.mark.parametrize("states,expected", [
    (["STATIC_SUPPORTED", "STATIC_SUPPORTED"], "STATIC_SUPPORTED"),
    (["STATIC_SUPPORTED", "VIOLATED"], "VIOLATED"),
    (["STATIC_SUPPORTED", "UNKNOWN"], "UNKNOWN"),
    (["STATIC_SUPPORTED", "EXTERNAL_EVIDENCE_REQUIRED"], "EXTERNAL_EVIDENCE_REQUIRED"),
    (["STATIC_SUPPORTED", "PARTIAL"], "PARTIAL"),
])
def test_one_requirement_one_result_with_every_criterion(store, sample_run, states, expected):
    _, run, eid = sample_run
    req = baseline(store, run["id"], eid)
    pending = result_matrix(store, run["id"])["requirements"]
    assert len(pending) == 1
    assert pending[0]["implementation_status"] == "NOT_CHECKED"
    assert pending[0]["checked_criteria"] == 0
    assert len(pending[0]["criterion_checks"]) == 2
    tid = check_task(store, run["id"], req, eid)
    assert result_matrix(store, run["id"])["requirements"][0]["implementation_status"] == "CHECKING"
    store.commit_output(tid, "requirement_checker", StageOutput(
        records=assessments(req, eid, states), summary="检查完成"))
    rows = result_matrix(store, run["id"])["requirements"]
    assert len(rows) == 1
    assert rows[0]["id"] == req.id
    assert rows[0]["requirement_number"] == pending[0]["requirement_number"] == "SR-001"
    assert rows[0]["implementation_status"] == expected
    assert rows[0]["checked_criteria"] == rows[0]["total_criteria"] == 2
    html = product_report("订单", {"records": []}, {"requirements": rows, "findings": []}, [])
    assert html.count("<article") == 2  # One requirement and its one implementation result.
    assert html.count("SR-001") == 2


def test_findings_must_link_to_current_requirement_and_checks_cannot_be_repeated(store, sample_run):
    _, run, eid = sample_run
    req = baseline(store, run["id"], eid)
    tid = check_task(store, run["id"], req, eid)
    finding = Finding(title="隔离缺失", module="订单", finding_type="IMPLEMENTATION_GAP",
                      evidence_ids=[eid], rationale="实现缺口", impact="数据泄露", recommendation="隔离")
    checks = assessments(req, eid, ["STATIC_SUPPORTED", "VIOLATED"])
    with pytest.raises(ValueError, match="关联当前需求"):
        store.commit_output(tid, "requirement_checker", StageOutput(records=checks + [finding], summary="检查"))
    assert not store.records(run["id"], "assessment")
    finding.requirement_ids = [req.id]
    store.commit_output(tid, "requirement_checker", StageOutput(records=checks + [finding], summary="检查"))
    matrix = result_matrix(store, run["id"])
    assert matrix["requirements"][0]["finding_ids"] == [finding.id]
    assert matrix["findings"][0]["requirement_numbers"] == ["SR-001"]
    retry = check_task(store, run["id"], req, eid, attempt=1)
    with pytest.raises(ValueError, match="已经提交"):
        store.commit_output(retry, "requirement_checker", StageOutput(
            records=assessments(req, eid, ["STATIC_SUPPORTED", "STATIC_SUPPORTED"]), summary="重复"))


def test_failed_check_keeps_unchecked_requirement_in_matrix(store, sample_run):
    _, run, eid = sample_run
    req = baseline(store, run["id"], eid)
    tid = check_task(store, run["id"], req, eid)
    store.fail_task(tid, "模型失败")
    row = result_matrix(store, run["id"])["requirements"][0]
    assert row["implementation_status"] == "INCOMPLETE"
    assert row["checked_criteria"] == 0
    assert all(c["implementation_status"] == "NOT_CHECKED" for c in row["criterion_checks"])


@pytest.mark.parametrize('status', ['STATIC_SUPPORTED', 'PARTIAL', 'VIOLATED'])
def test_design_text_alone_cannot_support_implementation_claim(store, sample_run, status):
    _, run, _ = sample_run
    doc = store.add_evidence(run['id'], 'document', 'design.md:1', '必须校验权限', {})
    req = baseline(store, run['id'], doc)
    tid = check_task(store, run['id'], req, doc)
    with pytest.raises(ValueError, match='实际读取的代码'):
        store.commit_output(tid, 'requirement_checker', StageOutput(
            records=assessments(req, doc, [status, status]), summary='文档不能证明实现'))
    assert not store.records(run['id'], 'assessment')


@pytest.mark.parametrize('verdict,label', [('SUPPORTED', 'Supported'), ('NEEDS_EVIDENCE', 'Needs information'), ('REJECTED', 'Needs correction')])
def test_requirement_review_visible_without_changing_requirement_or_implementation(store, sample_run, verdict, label):
    from security_auditor.domain import Review
    _, run, eid = sample_run
    req = baseline(store, run['id'], eid)
    pending = result_matrix(store, run['id'])['requirements'][0]
    assert pending['requirement_review_status'] == 'NOT_REVIEWED'
    tid = store.add_task(run['id'], 'requirement_review', {'subject_ids': [req.id]})
    store.start_task(tid)
    store.note_read(tid, eid)
    review = Review(title='需求复核', subject_id=req.id, verdict=verdict, checked_rules=['scope'],
                    rationale='<script>条件不符</script>', evidence_ids=[eid])
    store.commit_output(tid, 'requirement_reviewer', StageOutput(records=[review], summary='复核完成'))
    row = result_matrix(store, run['id'])['requirements'][0]
    assert row['requirement_review_status'] == verdict
    assert row['requirement_reviews'][0]['rationale'] == review.rationale
    assert row['id'] == req.id and row['statement'] == req.statement
    assert row['requirement_number'] == pending['requirement_number']
    assert row['implementation_status'] == 'NOT_CHECKED'
    html = product_report('project', {'records': [], 'run': {'language': 'en'}}, {'requirements': [row], 'findings': []}, [])
    assert 'Requirement review' in html and label in html
    assert '&lt;script&gt;' in html and '<script>' not in html
