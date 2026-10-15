"""Validate source integrity and declared review consistency, never entailment.

Semantic decisions belong to the model. These checks cannot prove that a
quotation implies a claim; they prevent missing, invented or contradictory
structured results from being committed as a successful review.
"""
from collections import Counter


def uses_source_contract(role: str, version: str | None) -> bool:
    """Explicit protocol compatibility; a method revision must not disable it."""
    if role not in {'design_analyst', 'requirement_generator', 'requirement_reviewer'}:
        return False
    if version not in {None, '0.2.0', '0.3.0', '0.4.0', '0.5.0', '0.5.1', '0.6.0'}:
        raise ValueError('未登记文档技能的来源协议版本')
    return version in {'0.4.0', '0.5.0', '0.5.1', '0.6.0'}


def validate_source_contract(store, task, role, record, known, read_ids):
    obligations = getattr(record, 'source_obligations', [])
    judgments = getattr(record, 'claim_reviews', [])
    required = uses_source_contract(role, task.get('skill_version'))
    if role in {'design_analyst', 'requirement_generator'} and required and not obligations:
        raise ValueError('缺少结构化原文约束')
    if role == 'requirement_reviewer' and required and not judgments:
        raise ValueError('缺少逐项来源复核')
    for item in [*obligations, *judgments]:
        for quote in item.quotes:
            if quote.evidence_id not in record.evidence_ids + getattr(record, 'counter_evidence_ids', []):
                raise ValueError('结构化引文未列入记录来源')
            if quote.evidence_id not in read_ids:
                raise ValueError('结构化引文来源尚未实际读取')
            source = store.read_evidence(task['run_id'], quote.evidence_id)
            if source['source_type'] not in {'document', 'standard'}:
                raise ValueError('需求来源引文必须来自设计文档或标准')
            if not quote.text.strip() or quote.text not in source['content']:
                raise ValueError('结构化引文不是来源中的连续原文')
    if role == 'requirement_generator' and obligations:
        expected = Counter([record.statement, *record.acceptance_criteria])
        if Counter(item.claim for item in obligations) != expected:
            raise ValueError('原文约束必须逐一对应需求正文与全部验收项')
    if role == 'requirement_reviewer' and judgments:
        target = known[record.subject_id]
        if target.get('kind') != 'requirement':
            raise ValueError('逐项来源复核对象必须为需求')
        expected = Counter([target['statement'], *target['acceptance_criteria']])
        if Counter(item.claim for item in judgments) != expected:
            raise ValueError('来源复核必须逐一对应需求正文与全部验收项')
        for item in judgments:
            if required and not item.logic_comparisons:
                raise ValueError('缺少原文与需求的双边逻辑对照')
            for pair in item.logic_comparisons:
                opposite = ({pair.source_polarity, pair.claim_polarity} == {'POSITIVE', 'NEGATIVE'})
                quantifier_changed = ({pair.source_quantifier, pair.claim_quantifier} == {'ALL', 'ANY'})
                if opposite and item.status not in {'CONTRADICTED', 'UNSUPPORTED'}:
                    raise ValueError('双边条件方向相反却未声明冲突')
                if quantifier_changed and item.status not in {'CONTRADICTED', 'UNSUPPORTED'}:
                    raise ValueError('双边量词不同却未声明问题')
            if (item.status == 'SUPPORTED') != (not item.issue_codes):
                raise ValueError('逐项来源状态与问题清单矛盾')
            if item.status == 'UNRESOLVED' and 'MISSING_CONTEXT' not in item.issue_codes:
                raise ValueError('未决来源复核须明确材料缺口')
        statuses = {item.status for item in judgments}
        expected_verdict = ('REJECTED' if statuses & {'CONTRADICTED', 'UNSUPPORTED'} else
                            'NEEDS_EVIDENCE' if 'UNRESOLVED' in statuses else 'SUPPORTED')
        if record.verdict != expected_verdict:
            raise ValueError('最终来源复核判定与逐项结果矛盾')
