"""Reader-facing reports use the run language; model results are never translated."""
from html import escape
import json

from .localization import tr
from .domain import compliance_candidate

STATUS = {
    'STATIC_SUPPORTED': '代码符合', 'VIOLATED': '未实现', 'PARTIAL': '部分实现',
    'UNKNOWN': '代码检查依据不足', 'NOT_CODE_VERIFIABLE': '无法通过代码验证',
    'EXTERNAL_EVIDENCE_REQUIRED': '无法通过代码验证',
    'NOT_CHECKED': '未检查', 'CHECKING': '检查中', 'INCOMPLETE': '检查未完成',
}
ORIGIN = {'EXPLICIT_DESIGN': '设计文档', 'INFERRED_SECURITY': '安全分析', 'PCI_DSS': 'PCI DSS'}
FINDING_STATUS = {'STATIC_SUPPORTED': '静态复核支持', 'FALSE_POSITIVE': '已排除',
                  'NEEDS_EVIDENCE': '待确认', 'CANDIDATE': '待确认'}
REQUIREMENT_REVIEW = {'SUPPORTED': '有依据', 'REJECTED': '需修正',
                      'NEEDS_EVIDENCE': '待补充', 'NOT_REVIEWED': '尚未复核'}


def product_report(project_name: str, data: dict, matrix: dict, sources: list[dict]) -> str:
    selected = data.get('run', {}).get('language', data.get('run', {}).get('snapshot', {}).get('language', 'en'))
    source_by_id = {s['id']: s for s in sources}

    def text(value):
        return escape(str(value or ''))

    def t(value):
        return text(tr(value, selected))

    def source_blocks(row, code=False):
        references = dict.fromkeys([*row['evidence_ids'], *row.get('counter_evidence_ids', [])])
        chosen = [source_by_id[eid] for eid in references if eid in source_by_id]
        chosen = [s for s in chosen if (s['source_type'] == 'code') == code]
        def label(source):
            title = source['locator']
            if source['source_type'] == 'standard':
                origin = source.get('metadata', {}).get('source', {})
                pages = origin.get('pdf_pages', []) if isinstance(origin, dict) else []
                page_text = (' · PDF 第 ' + '、'.join(map(str, pages)) + ' 页' if selected == 'zh-CN'
                             else ' · PDF pages ' + ', '.join(map(str, pages))) if pages else ''
                title = 'PCI DSS ' + title + page_text
            return text(title)
        return ''.join(f"<details><summary>{label(s)}</summary><pre>{text(s['content'])}</pre></details>"
                       for s in chosen) or (f"<p>{t('未定位到代码')}</p>" if code else '')

    sections, rows = [], []
    decisions = [r for r in data.get('records', []) if r['kind'] == 'applicability']
    def clause_ids(task):
        scope = task.get('scope', {})
        return (json.loads(scope) if isinstance(scope, str) else scope).get('clause_ids', [])
    clauses = {cid for task in data.get('tasks', []) if task['stage'] == 'pci_mapping' for cid in clause_ids(task)}
    matching = [task for task in data.get('tasks', []) if task['stage']=='pci_mapping' and
                (json.loads(task['scope']) if isinstance(task.get('scope'), str) else task.get('scope', {})).get('requirement_id')]
    relevance_labels = {'RELEVANT':'相关','POTENTIALLY_RELEVANT':'可能相关','UNRELATED':'不相关','UNKNOWN':'待确定'}
    labels = {'APPLICABLE': '适用', 'NOT_APPLICABLE': '不适用', 'UNDETERMINED': '待确定'}
    if clauses or matching:
        counts = ' · '.join(f'{t(label)} {sum(r["status"] == state for r in decisions)}' for state, label in labels.items())
        items = ''.join(f'<li>PCI DSS {text(r["clause_id"])}: {t(relevance_labels.get(r.get("relevance", "UNKNOWN"), "待确定"))} · {t(labels[r["status"]])}<p>{text(r["rationale"])}</p>'
                        + ''.join(f'<p>{text(f)}</p>' for f in r.get('applicability_conditions', []))
                        + ''.join(f'<p>{text(f)}</p>' for f in r['missing_facts']) + '</li>' for r in decisions if r.get('control_scope') not in {'NON_CODE', 'UNKNOWN'}
                        and r.get('relevance') != 'UNRELATED' and r['status'] != 'NOT_APPLICABLE')
        progress = (f'{t("标准匹配")} {sum(task["status"]=="SUCCEEDED" for task in matching)} / {len(matching)}'
                    if matching else f'{len(decisions)} / {len(clauses)} · {counts}')
        rows.append(f'<details><summary>PCI DSS · {progress}</summary><ul>{items}</ul></details>')

    def matched_clauses(req):
        matches = req.get('compliance_matches', [])
        if not matches:
            return ''
        return f'<h4>{t("相关条款")}</h4>' + ''.join(
            f'<p>PCI DSS {text(r["clause_id"])} · {t(relevance_labels.get(r.get("relevance", "UNKNOWN"), "待确定"))}'
            f' · {t(labels[r["status"]])}</p><p>{text(r["rationale"])}</p>'
            + ''.join(f'<p>{text(condition)}</p>' for condition in r.get('applicability_conditions', []))
                        + ''.join(f'<p>{text(fact)}</p>' for fact in r.get('missing_facts', [])) for r in matches
                        if compliance_candidate(r))
    for req in matrix['requirements']:
        criteria = ''.join(f'<li>{text(c)}</li>' for c in req['acceptance_criteria'])
        review = f'<h4>{t("需求复核")}</h4><p>{t(REQUIREMENT_REVIEW[req.get("requirement_review_status", "NOT_REVIEWED")])}</p>'
        review += ''.join(f'<p>{text(r["rationale"])}</p>{source_blocks(r)}'
                          for r in req.get('requirement_reviews', []))
        rows.append(
            f'<article id="requirement-{text(req["id"])}"><h3>{text(req["requirement_number"])} '
            f'{text(req["title"])}</h3><p>{text(req["statement"])}</p>'
            + ''.join(f'<p>{text(c)}</p>' for c in req.get('applicability_conditions', []))
            + (f'<p>{t("标准控制")}: {text(req["standard_control_id"])}</p>' if req.get('standard_control_id') else '')
            +
            f'<h4>{t("验收要求")}</h4><ul>{criteria}</ul><h4>{t("需求来源")}</h4>'
            f'<p>{t(ORIGIN.get(req["origin"], req["origin"]))} {text(", ".join(req["clause_ids"]))}</p>'
            f'{source_blocks(req)}{matched_clauses(req)}{review}</article>')
    sections.append(('安全需求', rows))
    rows = []
    for artifact in data.get('model_artifacts', []):
        if artifact['artifact_type'] != 'threat_model':
            continue
        for field, label in [('key_risks', '主要风险'), ('threat_actors', '攻击者'),
                             ('trust_boundaries', '信任边界'), ('entry_points', '攻击入口')]:
            rows.append(f'<article><h3>{t(label)}</h3><ul>' + ''.join(
                f'<li>{text(value)}</li>' for value in artifact['data'].get(field, [])) + '</ul></article>')
    for threat in [r for r in data['records'] if r['kind'] == 'threat']:
        rows.append(f'<article><h3>{text(threat["title"])}</h3>' + ''.join(
            f'<p>{t(label)}: {text(value)}</p>' for label, value in [
                ('攻击者', threat['attacker']), ('攻击入口', threat['entrypoint']),
                ('信任边界', threat['trust_boundary']), ('攻击前提', '; '.join(threat['preconditions'])),
                ('影响', threat['impact'])]) + '</article>')
    sections.append(('威胁建模', rows))
    rows = []
    for req in matrix['requirements']:
        checks = ''.join(f'<li><p>{text(c["acceptance_criterion"])}: {t(STATUS[c["implementation_status"]])}</p>'
                         f'<p>{text(c["rationale"])}</p></li>' for c in req['criterion_checks'])
        count = (f'{req["checked_criteria"]} / {req["total_criteria"]} 验收项已检查' if selected == 'zh-CN'
                 else f'{req["checked_criteria"]} / {req["total_criteria"]} acceptance criteria checked')
        rows.append(f'<article><h3>{text(req["requirement_number"])} {text(req["title"])}</h3>'
                    f'<p>{text(req["statement"])}</p><p>{t(STATUS[req["implementation_status"]])}</p>'
                    f'<p>{text(count)}</p><a href="#requirement-{text(req["id"])}">'
                    f'{t("查看需求来源")}</a>{matched_clauses(req)}<ul>{checks}</ul></article>')
    sections.append(('需求实现情况', rows))
    rows, excluded = [], []
    for finding in matrix['findings']:
        target = excluded if finding.get('review_status') == 'FALSE_POSITIVE' else rows
        numbers = ', '.join(finding.get('requirement_numbers', []))
        target.append(f'<article><h3>{text(finding["title"])}</h3>'
                      f'<p>{t("复核结果")}: {t(FINDING_STATUS.get(finding.get("review_status"), "待确认"))}</p>'
                      f'<p>{t("影响")}: {text(finding["impact"])}</p>'
                      + (f'<p>{t("关联需求")}: {text(numbers)}</p>' if numbers else '')
                      + f'<p>{text(finding["rationale"])}</p><h4>{t("修复建议")}</h4>'
                      f'<p>{text(finding["recommendation"])}</p><h4>{t("相关代码")}</h4>'
                      f'{source_blocks(finding, True)}</article>')
    if excluded:
        if not rows:
            rows.append(f'<p>{t("暂无待处理安全风险")}</p>')
        rows.append(f'<details><summary>{t("已排除的候选问题")} ({len(excluded)})</summary>' + ''.join(excluded) + '</details>')
    sections.append(('安全风险', rows))
    code_only = data.get('run', {}).get('mode') == 'code_only'
    body = ''.join(f'<section><h2>{i}. {t(name)}</h2>' + (''.join(items) or
                   f'<p>{t("本次未检查" if code_only and i in {1, 3} else "暂无结果")}</p>') + '</section>'
                   for i, (name, items) in enumerate(sections, 1))
    return (f'<!doctype html><html lang="{selected}"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{t("安全设计审查报告")}</title>'
            '<style>body{font-family:sans-serif;max-width:960px;margin:40px auto;padding:0 24px;color:#19324b;'
            'line-height:1.8}section{margin:40px 0}article{border-top:1px solid #dce3ec;padding:16px 0}'
            'h4{margin-bottom:8px}pre{background:#f3f6fa;padding:16px;white-space:pre-wrap;overflow-wrap:anywhere}'
            'summary{color:#176e67;cursor:pointer}</style></head>'
            f'<body><h1>{text(project_name)}</h1>{body}</body></html>')
