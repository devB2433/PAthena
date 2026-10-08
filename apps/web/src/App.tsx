import { useEffect, useState } from 'react';
import { Alert, Button, ConfigProvider, Drawer, Empty, Input, InputNumber, Modal, Select, Space, Table, Tag, Upload, message } from 'antd';
import { FolderOpenOutlined, PlayCircleOutlined, SafetyCertificateOutlined, UploadOutlined } from '@ant-design/icons';

import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import { translate, format, type Language } from './i18n';

type Project = { id: string; name: string };
type Budget = { max_requests: number | null; max_tokens: number | null };
type Run = { id: string; status: string; mode: string; language?: Language; display_error?: string; error: string | null; budget?: Budget; repository_id?: string; pause_requested?: boolean; issue?: { code: string; message: string }; recovery?: { attempts: number; wake_at: number }; controls?: { can_resume: boolean; can_update_budget: boolean; budget_exhausted: boolean; requires_new_run: boolean }; usage?: { requests: number; tokens: number; known_tokens?: number; unknown_tokens?: number; unknown_requests?: number; reserved_tokens?: number } };
type Row = {
  id: string; kind: string; title: string; rationale: string; evidence_ids: string[];
  module?: string; statement?: string; acceptance_criteria?: string[]; origin?: string;
  clause_ids?: string[]; requirement_id?: string; implementation_status?: string;
  impact?: string; recommendation?: string; severity?: string; checks?: Row[];
  review_status?: string; attacker?: string; entrypoint?: string; trust_boundary?: string;
  preconditions?: string[]; attack_preconditions?: string[]; acceptance_criterion?: string;
  requirement_number?: string; requirement_numbers?: string[]; criterion_checks?: Row[];
  checked_criteria?: number; total_criteria?: number; finding_ids?: string[];
  requirement_review_status?: string; requirement_reviews?: Row[]; verdict?: string; counter_evidence_ids?: string[];
  clause_id?: string; status?: string; missing_facts?: string[]; relevance?: string;
  requirement_ids?: string[]; applicability_conditions?: string[]; compliance_matches?: Row[];
};
type Task = { stage: string; wave: string; status: string; scope?: string | { clause_ids?: string[]; requirement_id?: string } };
type ModelArtifact = { artifact_type: string; data: { threat_actors?: string[]; trust_boundaries?: string[]; entry_points?: string[]; key_risks?: string[] } };
type SourceMetadata = { line_start?: number; provenance?: { page_no?: number }[]; source?: { pdf_pages?: number[] } };
type Source = { id: string; source_type: string; locator: string; content?: string; metadata?: string | SourceMetadata };
type Phase = 'requirements' | 'threats' | 'implementation' | 'vulnerabilities';
const phaseDefinitions: { id: Phase; title: string; stages: string[] }[] = [
  { id: 'requirements', title: '安全需求', stages: ['design_model', 'requirements', 'pci_mapping', 'pci_requirements', 'requirement_review'] },
  { id: 'threats', title: '威胁建模', stages: ['threat_model'] },
  { id: 'implementation', title: '需求实现情况', stages: ['requirement_check'] },
  { id: 'vulnerabilities', title: 'Findings', stages: ['vulnerability_research'] },
];
const labels: Record<string, string> = {
  PENDING: '待运行', RUNNING: '分析中', COMPLETED: '已完成', SUCCEEDED: '已完成',
  FAILED: '未完成', PAUSED: '已暂停', WAITING: '等待模型恢复', SKIPPED: '未运行', STATIC_SUPPORTED: '代码符合',
  VIOLATED: '未实现', PARTIAL: '部分实现', UNKNOWN: '无法确认',
  EXTERNAL_EVIDENCE_REQUIRED: '需检查外部配置', HIGH: '高', MEDIUM: '中', LOW: '低', CRITICAL: '严重',
  NOT_CHECKED: '未检查', CHECKING: '检查中', INCOMPLETE: '检查未完成',
};
const colors: Record<string, string> = {
  COMPLETED: 'green', SUCCEEDED: 'green', STATIC_SUPPORTED: 'green', VIOLATED: 'red',
  FAILED: 'red', RUNNING: 'blue', PARTIAL: 'orange', PAUSED: 'orange', WAITING: 'orange', HIGH: 'red', CRITICAL: 'red',
};
const originDefinitions: Record<string, string> = { EXPLICIT_DESIGN: '设计文档', INFERRED_SECURITY: '安全分析', PCI_DSS: 'PCI DSS' };
const requirementReviewLabels: Record<string, string> = { SUPPORTED: '有依据', REJECTED: '需修正', NEEDS_EVIDENCE: '待补充', NOT_REVIEWED: '尚未复核' };
function sourceLabel(source: Source, language: Language) {
  let meta: SourceMetadata = {};
  try { meta = typeof source.metadata === 'string' ? JSON.parse(source.metadata) : source.metadata || {}; } catch { /* Older sources may lack location metadata. */ }
  if (source.source_type === 'standard') {
    const pages = meta.source?.pdf_pages;
    return `PCI DSS ${source.locator}${pages?.length ? (language === 'zh-CN' ? ` · PDF 第 ${pages.join('、')} 页` : ` · PDF pages ${pages.join(', ')}`) : ''}`;
  }
  if (source.source_type !== 'document') return source.locator;
  const file = source.locator.split(' / ')[0];
  const page = meta.provenance?.[0]?.page_no;
  if (page) return language === 'zh-CN' ? `${file} · ${file.endsWith('.pptx') ? '幻灯片' : '第'} ${page} ${file.endsWith('.pptx') ? '' : '页'}`.trim() : `${file} · ${file.endsWith('.pptx') ? 'Slide' : 'Page'} ${page}`;
  if (/\.(md|txt)$/i.test(file) && meta.line_start) return language === 'zh-CN' ? `${file} · 第 ${meta.line_start} 行` : `${file} · Line ${meta.line_start}`;
  return file;
}
const renderStatusTag = (state: string, language: Language) => <Tag color={colors[state]}>{translate(labels[state] || state, language)}</Tag>;
async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + url, init);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Operation failed. Check your input.');
  return data;
}
const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export default function App() {
  const [language, setLanguage] = useState<Language>('en');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [editedLanguage, setEditedLanguage] = useState<Language>('en');
  const t = (text: string) => translate(text, language);
  const tf = (text: string, values: Record<string, string | number>) => format(text, language, values);
  const phases = phaseDefinitions.map(p => ({ ...p, title: t(p.title) }));
  const originLabels = Object.fromEntries(Object.entries(originDefinitions).map(([k, v]) => [k, t(v)]));
  const requirementReviewTag = (state = 'NOT_REVIEWED') => <Tag color={({ SUPPORTED: 'green', REJECTED: 'red', NEEDS_EVIDENCE: 'orange' } as Record<string, string>)[state]}>{t(requirementReviewLabels[state] || state)}</Tag>;
  const statusTag = (state: string) => renderStatusTag(state, language);
  const sourceTitle = (source: Source) => sourceLabel(source, language);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [records, setRecords] = useState<Row[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [assets, setAssets] = useState<{ id: string; name: string }[]>([]);
  const [matrix, setMatrix] = useState<{ requirements: Row[]; findings: Row[]; model_artifacts?: ModelArtifact[] }>({ requirements: [], findings: [] });
  const [sources, setSources] = useState<Source[]>([]);
  const [phase, setPhase] = useState<Phase>('requirements');
  const [filter, setFilter] = useState('');
  const [showExcluded, setShowExcluded] = useState(false);
  const [detail, setDetail] = useState<{ row: Row; phase: Phase } | null>(null);
  const [detailSources, setDetailSources] = useState<Source[]>([]);
  const [newOpen, setNewOpen] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [budgetOpen, setBudgetOpen] = useState(false);
  const [budgetTarget, setBudgetTarget] = useState<'new' | 'current'>('new');
  const [newBudget, setNewBudget] = useState<Budget>({ max_requests: null, max_tokens: null });
  const [editedBudget, setEditedBudget] = useState<Budget>(newBudget);
  const [clock, setClock] = useState(Date.now());
  const [repo, setRepo] = useState<string>();
  const [settings, setSettings] = useState<{ repositories: string[]; language?: Language; default_budget?: Budget; budget_enforced?: boolean }>({ repositories: [] });
  const [msg, holder] = message.useMessage();
  const refreshProjects = async () => setProjects((await api<{ items: Project[] }>('/projects')).items);

  useEffect(() => {
    Promise.all([refreshProjects(), api<typeof settings>('/settings').then(s => { setSettings(s); setLanguage(s.language || 'en'); if (s.default_budget) setNewBudget(s.default_budget); })]).catch(e => msg.error(t(e.message)));
  }, []);
  useEffect(() => {
    document.documentElement.lang = language;
    document.title = t('安全设计审查');
  }, [language]);
  useEffect(() => {
    if (!project) return;
    let current = true;
    setRun(null); setRuns([]); setRecords([]); setTasks([]); setSources([]); setAssets([]);
    setMatrix({ requirements: [], findings: [] }); setDetail(null); setDetailSources([]); setFilter(''); setRepo(undefined);
    Promise.all([
      api<{ items: typeof assets }>(`/projects/${project.id}/assets`),
      api<{ items: Run[] }>(`/projects/${project.id}/runs`),
    ]).then(([a, r]) => {
      if (!current) return;
      setAssets(a.items); setRuns(r.items); setRun(r.items[0] || null);
      if (r.items[0]?.repository_id) setRepo(r.items[0].repository_id);
      setPhase(r.items[0]?.mode === 'code_only' ? 'threats' : 'requirements');
    }).catch(e => { if (current) msg.error(t(e.message)); });
    return () => { current = false; };
  }, [project?.id]);

  useEffect(() => {
    if (run?.status !== 'WAITING') return;
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [run?.status]);

  const refreshRun = async (pid: string, rid: string, isCurrent = () => true) => {
    const base = `/projects/${pid}/runs/${rid}`;
    const [r, rs, ts, ss, mx] = await Promise.all([
      api<Run>(base), api<{ items: Row[] }>(base + '/records'),
      api<{ items: Task[] }>(base + '/tasks'), api<{ items: Source[] }>(base + '/evidence'),
      api<typeof matrix>(base + '/matrix'),
    ]);
    if (isCurrent()) {
      setRun(r); setRuns(old => old.map(item => item.id === r.id ? r : item));
      setRecords(rs.items); setTasks(ts.items); setSources(ss.items); setMatrix(mx);
    }
    return r;
  };
  useEffect(() => {
    if (!project || !run) return;
    setShowExcluded(false);
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const current = await refreshRun(project.id, run.id, () => !stopped);
        if (!stopped && ['PENDING', 'RUNNING', 'WAITING'].includes(current.status)) timer = setTimeout(tick, 1800);
      } catch (e) { if (!stopped) msg.error(t((e as Error).message)); }
    };
    void tick();
    return () => { stopped = true; clearTimeout(timer); };
  }, [project?.id, run?.id, run?.status]);

  useEffect(() => {
    setDetailSources([]);
    if (!detail || !project) return;
    let current = true;
    const references = detail.phase === 'requirements' ? [...detail.row.evidence_ids,
      ...(detail.row.requirement_reviews || []).flatMap(r => [...r.evidence_ids, ...(r.counter_evidence_ids || [])])] : detail.row.evidence_ids;
    const ids = [...new Set(references)].filter(id => {
      const source = sources.find(s => s.id === id);
      return detail.phase === 'vulnerabilities' ? source?.source_type === 'code'
        : detail.phase === 'requirements' ? source?.source_type !== 'code' : true;
    });
    Promise.all(ids.map(id => api<Source>(`/projects/${project.id}/evidence/${id}`)))
      .then(items => { if (current) setDetailSources(items); })
      .catch(e => { if (current) msg.error(t(e.message)); });
    return () => { current = false; };
  }, [detail, project?.id]);

  const action = async (fn: () => Promise<void>) => {
    setBusy(true); try { await fn(); } catch (e) { msg.error(t((e as Error).message)); } finally { setBusy(false); }
  };
  const create = () => action(async () => {
    const p = await api<Project>('/projects', post({ name }));
    await refreshProjects(); setProject(p); setNewOpen(false); setName(''); setPhase('requirements');
  });
  const start = () => action(async () => {
    if (!project) return;
    const r = await api<Run>(`/projects/${project.id}/runs`, {
      ...post({ mode: repo ? (assets.length ? 'full' : 'code_only') : 'requirements_only', repository_id: repo ?? null, budget: newBudget }),
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
    });
    setRun(r); setRuns(old => [r, ...old]); setPhase(r.mode === 'code_only' ? 'threats' : 'requirements');
  });
  const control = (verb: string) => action(async () => {
    if (!project || !run) return;
    await api(`/projects/${project.id}/runs/${run.id}/${verb}`, post({}));
    await refreshRun(project.id, run.id);
  });
  const startChecks = () => action(async () => {
    if (!project || !run || !repo) return;
    const r = await api<Run>(`/projects/${project.id}/runs`, {
      ...post({ mode: 'implementation_only', repository_id: repo, baseline_run_id: run.id, budget: newBudget }),
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
    });
    setRun(r); setRuns(old => [r, ...old]); setPhase('implementation');
  });
  const openBudget = (target: 'new' | 'current') => {
    const selected = target === 'current' && run?.budget ? run.budget : newBudget;
    setBudgetTarget(target); setEditedBudget({ max_requests: selected.max_requests, max_tokens: selected.max_tokens }); setBudgetOpen(true);
  };
  const saveBudget = () => action(async () => {
    if (budgetTarget === 'current' && project && run) {
      const changed = await api<Run>(`/projects/${project.id}/runs/${run.id}/budget`, post(editedBudget));
      if (run.issue?.code === 'budget' && changed.controls?.can_resume) {
        await api(`/projects/${project.id}/runs/${run.id}/resume`, post({}));
      }
      await refreshRun(project.id, run.id);
    } else setNewBudget(editedBudget);
    setBudgetOpen(false);
  });
  const open = (row: Row, output: Phase = phase) => setDetail({ row: output === 'implementation'
    ? { ...row, evidence_ids: [...new Set((row.criterion_checks || []).flatMap(c => c.evidence_ids))] }
    : row, phase: output });
  const locations = (row: Row, code = false) => row.evidence_ids
    .map(id => sources.find(s => s.id === id))
    .filter((s): s is Source => !!s && (code ? s.source_type === 'code' : s.source_type !== 'code'));
  const matches = (row: Row) => !filter || [row.requirement_number, row.title, row.module, row.statement,
    row.acceptance_criteria?.join(' '), row.acceptance_criterion].join(' ').includes(filter);
  const requirements = matrix.requirements;
  const pciDecisions = records.filter(r => r.kind === 'applicability');
  const pciInventory = new Set(tasks.filter(t => t.stage === 'pci_mapping').flatMap(t => {
    const scope = typeof t.scope === 'string' ? JSON.parse(t.scope) : t.scope;
    return scope?.clause_ids || [];
  })).size;
  const pciLabels: Record<string, string> = { APPLICABLE: '适用', NOT_APPLICABLE: '不适用', UNDETERMINED: '待确定' };
  const relevanceLabels: Record<string, string> = { RELEVANT: '相关', POTENTIALLY_RELEVANT: '可能相关', UNRELATED: '不相关', UNKNOWN: '待确定' };
  const pciMatchTasks = tasks.filter(task => task.stage === 'pci_mapping' && !!(typeof task.scope === 'string' ? JSON.parse(task.scope) : task.scope)?.requirement_id);
  const relatedClauseCount = new Set(pciDecisions.filter(r => ['RELEVANT', 'POTENTIALLY_RELEVANT'].includes(r.relevance || '')).map(r => r.clause_id)).size;
  const threats = records.filter(r => r.kind === 'threat');
  const threatModels = (matrix.model_artifacts || []).filter(a => a.artifact_type === 'threat_model');
  const implementations = requirements;
  const findings = matrix.findings;
  const excludedFindings = findings.filter(f => f.review_status === 'FALSE_POSITIVE');
  const activeFindings = findings.filter(f => f.review_status !== 'FALSE_POSITIVE');
  const displayedFindings = showExcluded ? excludedFindings : activeFindings;
  const counts: Record<Phase, number> = { requirements: requirements.length, threats: threats.length || threatModels.reduce((n, a) => n + (a.data.key_risks?.length || 0), 0), implementation: implementations.length, vulnerabilities: activeFindings.length };
  const phaseState = (id: Phase) => {
    if (run?.mode === 'implementation_only' && id === 'requirements')
      return tasks.some(t => t.stage === 'requirement_baseline' && t.status === 'SUCCEEDED') ? t('已完成') : t('未完成');
    if (run?.mode === 'implementation_only' && id === 'vulnerabilities') return t('本次未扫描');
    if (run?.mode === 'code_only' && ['requirements', 'implementation'].includes(id)) return t('已跳过');
    const group = phases.find(p => p.id === id)!;
    const parents = tasks.filter(t => t.wave === 'stage' && group.stages.includes(t.stage));
    if (parents.some(t => t.status === 'FAILED')) return t('失败');
    if (run?.status === 'WAITING' && parents.some(t => !['SUCCEEDED', 'SKIPPED'].includes(t.status))) return t('等待模型恢复');
    if (parents.some(t => t.status === 'RUNNING')) return run?.status === 'PAUSED' ? t('已暂停') : run?.status === 'FAILED' ? t('中断') : t('分析中');
    const expectedCount = group.stages.filter(stage => stage !== 'pci_requirements' || parents.some(p => p.stage === stage)).length;
    if (parents.length === expectedCount && parents.every(t => ['SUCCEEDED', 'SKIPPED'].includes(t.status)))
      return parents.every(t => t.status === 'SKIPPED') ? t('未运行') : t('已完成');
    return parents.length ? t('未完成') : t('未运行');
  };
  const base = project && run ? `/api/v1/projects/${project.id}/runs/${run.id}` : '';
  const activeTitle = phases.find(p => p.id === phase)!.title;
  const empty = <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('暂无结果')} />;

  return <ConfigProvider locale={language === 'zh-CN' ? zhCN : enUS}><div className="workspace">{holder}
    <aside className="sidebar">
      <div className="brand"><SafetyCertificateOutlined /><span>{t('安全设计审查')}</span></div>
      <div className="project-label">{t('项目')}<Button type="text" size="small" onClick={() => setNewOpen(true)} aria-label={t('创建项目')}>＋</Button></div>
      <nav>{projects.map(p => <button key={p.id} className={`project-item ${project?.id === p.id ? 'selected' : ''}`} onClick={() => setProject(p)}><FolderOpenOutlined /><span>{p.name}</span></button>)}</nav>
      <div className="sidebar-footer">{t('开发预览')}</div>
    </aside>
    <main>
      <header className="topbar"><span>{t('项目工作台')}</span><Space><span>{t('安全设计审查')}</span><Button onClick={() => { setEditedLanguage(language); setSettingsOpen(true); }}>{t('系统设置')}</Button></Space></header>
      {!project ? <section className="welcome">
        <h1>{t('安全设计审查')}</h1>
        <div className="welcome-outputs">{phases.map((p, i) => <div key={p.id}><span>{i + 1}</span><h2>{p.title}</h2></div>)}</div>
        <Space><Button type="primary" size="large" onClick={() => setNewOpen(true)}>{t('创建项目')}</Button></Space>
      </section> : <>
        <section className="project-heading"><div><h1>{project.name}</h1></div>
          <Space>{!!matrix.requirements.length && <Button onClick={startChecks} loading={busy} disabled={!repo || !run || ['RUNNING', 'PENDING', 'WAITING'].includes(run.status)}>{t('检查已有需求')}</Button>}{settings.budget_enforced && <Button onClick={() => openBudget('new')}>{t('分析额度')}</Button>}<Button disabled={!run} onClick={() => window.open(base + '/reports/html', '_blank')}>{t('导出报告')}</Button><Button type="primary" icon={<PlayCircleOutlined />} onClick={start} loading={busy} disabled={(!assets.length && !repo) || ['RUNNING', 'PENDING', 'WAITING'].includes(run?.status || '')}>{run?.controls?.requires_new_run ? t('重新分析') : repo && !assets.length ? t('开始代码分析') : t('开始分析')}</Button></Space>
        </section>
        {run?.error && <Alert className="run-alert" type="warning" showIcon message={run.controls?.requires_new_run ? t('分析流程已更新，请重新分析。历史结果已保留。') : t(run.display_error || run.issue?.message || run.error)} />}
        <section className="materials-bar"><div><strong>{t('设计文档')}</strong><span>{assets.length ? assets.map(a => a.name).join('、') : t('未上传')}</span></div>
          <Space wrap><Select aria-label={t('选择代码仓库')} placeholder={t('选择代码仓库')} allowClear value={repo} onChange={setRepo} style={{ minWidth: 180 }} options={settings.repositories.map(id => ({ value: id, label: id }))} />
            <Upload showUploadList={false} accept=".docx,.pdf,.pptx,.md,.txt" customRequest={async options => {
              try {
                const form = new FormData(); form.append('file', options.file as File);
                await api(`/projects/${project.id}/documents`, { method: 'POST', body: form });
                setAssets((await api<{ items: typeof assets }>(`/projects/${project.id}/assets`)).items);
                options.onSuccess?.({}); msg.success(t('文档已上传'));
              } catch (e) { msg.error(t((e as Error).message)); options.onError?.(e as Error); }
            }}><Button icon={<UploadOutlined />}>{t('上传文档')}</Button></Upload>
          </Space>
        </section>
        {run?.language && run.language !== language && <Alert type="info" message={tf('历史分析使用 {language}，切换语言后请重新分析。', { language: run.language === 'zh-CN' ? '简体中文' : 'English' })} />}
        <section className="run-strip"><Space>{run ? statusTag(run.status) : <span>{t('尚未分析')}</span>}{runs.length > 0 && <Select aria-label={t('选择历史运行')} value={run?.id} onChange={id => { setDetail(null); setRun(runs.find(r => r.id === id)!); }} options={runs.map((r, i) => ({ value: r.id, label: tf('分析 {number}', { number: runs.length - i }) }))} />}</Space>
          {run && <Space>{run.budget && <span>{tf('已请求 {used} / {limit} 次', { used: run.usage?.requests || 0, limit: run.budget.max_requests ?? t('不限额') })}</span>}{run.status === 'WAITING' && run.recovery && <span>{tf('{seconds} 秒后重试', { seconds: Math.max(0, Math.ceil((run.recovery.wake_at * 1000 - clock) / 1000)) })}</span>}{['RUNNING', 'PENDING', 'WAITING'].includes(run.status) && <Button size="small" disabled={busy || run.pause_requested} onClick={() => control('pause')}>{run.pause_requested ? t('暂停中') : t('暂停')}</Button>}{['PAUSED', 'FAILED'].includes(run.status) && <>{run.controls?.can_update_budget && <Button size="small" disabled={busy} onClick={() => openBudget('current')}>{t('修改额度')}</Button>}{run.controls?.can_resume && <Button size="small" disabled={busy} onClick={() => control('resume')}>{['authentication', 'balance', 'unavailable', 'recovery_exhausted'].includes(run.issue?.code || '') ? t('重试连接') : run.issue?.code === 'output' ? t('重试未完成阶段') : t('继续分析')}</Button>}</>}</Space>}
        </section>
        {run?.budget && <div>{tf('已知用量：{tokens} tokens', { tokens: run.usage?.known_tokens?.toLocaleString() || 0 })}{run.usage?.reserved_tokens ? tf(' · 在途预留：{tokens}', { tokens: run.usage.reserved_tokens.toLocaleString() }) : ''}{run.usage?.unknown_requests ? tf(' · {requests} 次请求用量未知，估算 {tokens} tokens', { requests: run.usage.unknown_requests, tokens: run.usage.unknown_tokens?.toLocaleString() || 0 }) : ''}</div>}
        <nav className="phase-nav" aria-label={t('分析阶段')}>{phases.map((p, i) => <button key={p.id} className={`phase-step ${phase === p.id ? 'active' : ''}`} onClick={() => { setPhase(p.id); setFilter(''); }} aria-current={phase === p.id ? 'step' : undefined}>
          <span className="phase-number">{i + 1}</span><span className="phase-name">{p.title}<small>{phaseState(p.id)}</small></span><span className="phase-count">{counts[p.id]}</span>
        </button>)}</nav>
        <section className="data-surface">
          <div className="section-heading"><h2>{activeTitle}</h2><Input.Search placeholder={tf('搜索{title}', { title: activeTitle })} allowClear value={filter} onChange={e => setFilter(e.target.value)} style={{ width: 240 }} /></div>
          {phase === 'requirements' && <>{(pciInventory > 0 || pciMatchTasks.length > 0) && <details><summary>PCI DSS · {pciMatchTasks.length ? tf('已匹配 {done} / {total} 条需求', { done: pciMatchTasks.filter(task => task.status === 'SUCCEEDED').length, total: pciMatchTasks.length }) : tf('已判断 {done} / {total} 条款', { done: pciDecisions.length, total: pciInventory })}{pciMatchTasks.length ? <Tag>{t('相关条款')} {relatedClauseCount}</Tag> : Object.entries(pciLabels).map(([state, label]) => <Tag key={state}>{t(label)} {pciDecisions.filter(r => r.status === state).length}</Tag>)}</summary><Table<Row> rowKey="id" dataSource={pciDecisions} pagination={{ pageSize: 5 }} locale={{ emptyText: empty }} columns={[
            { title: t('条款'), dataIndex: 'clause_id', width: 110 },
            { title: t('相关性'), width: 110, render: (_, row) => t(relevanceLabels[row.relevance || 'UNKNOWN']) },
            { title: t('适用性'), width: 100, render: (_, row) => t(pciLabels[row.status || ''] || '待确定') },
            { title: t('匹配依据与条件'), render: (_, row) => <div><p>{row.rationale}</p>{row.applicability_conditions?.map(f => <p key={f}>{f}</p>)}{row.missing_facts?.map(f => <p key={f}>{f}</p>)}</div> },
          ]} /></details>}<Table<Row> rowKey="id" dataSource={requirements.filter(matches)} pagination={{ pageSize: 8 }} locale={{ emptyText: empty }} columns={[
            { title: t('需求编号'), dataIndex: 'requirement_number', width: 110 },
            { title: t('安全需求'), render: (_, row) => <button className="text-link" onClick={() => open(row)}>{row.statement || row.title}</button> },
            { title: t('模块'), dataIndex: 'module', width: 130 },
            { title: t('需求复核'), width: 110, render: (_, row) => requirementReviewTag(row.requirement_review_status) },
            { title: t('来源'), width: 240, render: (_, row) => <div className="origin"><Tag>{originLabels[row.origin || ''] || t('待确认')}</Tag>{row.clause_ids?.length ? <span>{row.clause_ids.join('、')}</span> : null}{locations(row).map(s => <button key={s.id} className="text-link source-location" onClick={() => open(row)}>{sourceTitle(s)}</button>)}</div> },
          ]} /></>}
          {phase === 'threats' && <>{threatModels.map((artifact, index) => <section key={index}>
            {(['key_risks', 'threat_actors', 'trust_boundaries', 'entry_points'] as const).map(field => <details key={field} open={field === 'key_risks'}>
              <summary>{({ key_risks: t('主要风险'), threat_actors: t('攻击者'), trust_boundaries: t('信任边界'), entry_points: t('攻击入口') })[field]}</summary>
              <ul>{artifact.data[field]?.filter(value => !filter || value.toLowerCase().includes(filter.toLowerCase())).map((value, item) => <li key={item}>{value}</li>)}</ul>
            </details>)}
          </section>)}{!!threats.length && <Table<Row> rowKey="id" dataSource={threats.filter(matches)} pagination={{ pageSize: 8 }} locale={{ emptyText: empty }} columns={[
            { title: t('威胁'), dataIndex: 'title', render: (v, row) => <button className="text-link" onClick={() => open(row)}>{v}</button> },
            { title: t('模块'), dataIndex: 'module', width: 130 }, { title: t('攻击入口'), dataIndex: 'entrypoint' },
            { title: t('攻击者'), dataIndex: 'attacker' }, { title: t('影响'), dataIndex: 'impact' },
          ]} />}{!threats.length && !threatModels.length && empty}</>}
          {phase === 'implementation' && <Table<Row> rowKey="id" dataSource={implementations.filter(matches)} pagination={{ pageSize: 8 }} locale={{ emptyText: empty }} columns={[
            { title: t('需求编号'), dataIndex: 'requirement_number', width: 110 },
            { title: t('安全需求'), render: (_, row) => <button className="text-link" onClick={() => open(row)}>{row.statement || row.title}</button> },
            { title: t('模块'), dataIndex: 'module', width: 130 },
            { title: t('实现情况'), width: 150, render: (_, row) => statusTag(row.implementation_status || 'NOT_CHECKED') },
            { title: t('验收项'), width: 130, render: (_, row) => tf('{checked} / {total} 已检查', { checked: row.checked_criteria || 0, total: row.total_criteria || 0 }) },
          ]} />}
          {phase === 'vulnerabilities' && <>
            {!!excludedFindings.length && <Button type="link" onClick={() => setShowExcluded(!showExcluded)}>{showExcluded ? t('返回 Findings 列表') : tf('已排除 {count} 项', { count: excludedFindings.length })}</Button>}
            <Table<Row> rowKey="id" dataSource={displayedFindings.filter(matches)} pagination={{ pageSize: 8 }} locale={{ emptyText: empty }} columns={[
            { title: t('Findings'), dataIndex: 'title', render: (v, row) => <button className="text-link" onClick={() => open(row)}>{v}</button> },
            { title: t('模块'), dataIndex: 'module', width: 120 }, { title: t('风险'), width: 90, render: (_, row) => statusTag(row.severity || 'UNKNOWN') },
            { title: t('关联需求'), width: 110, render: (_, row) => row.requirement_numbers?.length ? row.requirement_numbers.map(number => <button key={number} className="text-link source-location" onClick={() => { const req = requirements.find(r => r.requirement_number === number); if (req) open(req, 'implementation'); }}>{number}</button>) : '—' },
            { title: t('代码位置'), width: 210, render: (_, row) => locations(row, true).length ? locations(row, true).map(s => <button key={s.id} className="text-link source-location" onClick={() => open(row)}>{sourceTitle(s)}</button>) : t('未定位到代码') },
            { title: t('状态'), width: 100, render: (_, row) => <Tag>{row.review_status === 'STATIC_SUPPORTED' ? t('已复核') : row.review_status === 'FALSE_POSITIVE' ? t('已排除') : t('待确认')}</Tag> },
          ]} /></>}
        </section>
      </>}
    </main>
    <Modal title={t('系统设置')} open={settingsOpen} onCancel={() => setSettingsOpen(false)} okText={t('保存')} confirmLoading={busy} onOk={() => action(async () => {
      const changed = await api<typeof settings>('/settings', { ...post({ language: editedLanguage }), method: 'PUT' });
      setSettings(changed); setLanguage(editedLanguage); setSettingsOpen(false);
      if (project && run) await refreshRun(project.id, run.id);
      msg.success(translate('设置已保存', editedLanguage));
    })}>
      <label className="field-label">{t('语言')}</label><Select aria-label={t('语言')} value={editedLanguage} onChange={setEditedLanguage} options={[{ value: 'en', label: 'English' }, { value: 'zh-CN', label: '简体中文' }]} style={{ width: '100%' }} />
      <p>{t('后续分析使用所选语言直接生成内容。')}</p>
    </Modal>
    <Modal title={t('创建项目')} open={newOpen} onCancel={() => setNewOpen(false)} onOk={create} okText={t('创建')} confirmLoading={busy} okButtonProps={{ disabled: !name.trim() }}>
      <label className="field-label" htmlFor="project-name">{t('项目名称')}</label><Input id="project-name" placeholder={t('例如：支付子系统')} value={name} onChange={e => setName(e.target.value)} onPressEnter={() => name.trim() && create()} />
    </Modal>
    <Modal title={t('分析额度')} open={budgetOpen} onCancel={() => setBudgetOpen(false)} onOk={saveBudget} okText={budgetTarget === 'current' && run?.issue?.code === 'budget' && !run.controls?.requires_new_run ? t('保存并继续') : t('保存')} confirmLoading={busy}>
      <label className="field-label" htmlFor="request-budget">{t('最多请求次数（含重试）')}</label><InputNumber id="request-budget" min={1} precision={0} placeholder={t('不限额')} value={editedBudget.max_requests} onChange={value => setEditedBudget(old => ({ ...old, max_requests: value }))} />
      <label className="field-label" htmlFor="token-budget">{t('最多 tokens')}</label><InputNumber id="token-budget" min={1} precision={0} placeholder={t('不限额')} value={editedBudget.max_tokens} onChange={value => setEditedBudget(old => ({ ...old, max_tokens: value }))} />
      <p>{t('留空表示不限额。修改额度后，已有用量仍会累计。')}</p>
    </Modal>
    <Drawer title={detail ? phases.find(p => p.id === detail.phase)?.title : ''} width={640} open={!!detail} onClose={() => setDetail(null)}>
      {detail && <><h2>{detail.row.title}</h2>
        {detail.row.requirement_number && <p className="requirement-number">{detail.row.requirement_number}</p>}
        {['requirements', 'implementation'].includes(detail.phase) && !!detail.row.compliance_matches?.length && <><h3>{t('相关条款')}</h3>{detail.row.compliance_matches.map(match => <section key={match.id}><p>PCI DSS {match.clause_id} · {t(relevanceLabels[match.relevance || 'UNKNOWN'])} · {t(pciLabels[match.status || 'UNDETERMINED'])}</p><p>{match.rationale}</p>{match.applicability_conditions?.map(condition => <p key={condition}>{condition}</p>)}{match.missing_facts?.map(fact => <p key={fact}>{fact}</p>)}</section>)}</>}
        {detail.phase === 'requirements' && <><p>{detail.row.statement}</p><h3>{t('验收要求')}</h3><ul>{detail.row.acceptance_criteria?.map(c => <li key={c}>{c}</li>)}</ul><h3>{t('需求来源')}</h3><p>{originLabels[detail.row.origin || '']}{detail.row.clause_ids?.length ? ` · ${detail.row.clause_ids.join('、')}` : ''}</p>{detail.row.origin === 'INFERRED_SECURITY' && <p>{detail.row.rationale}</p>}</>}
        {detail.phase === 'threats' && <><dl className="detail-fields"><dt>{t('攻击者')}</dt><dd>{detail.row.attacker}</dd><dt>{t('攻击入口')}</dt><dd>{detail.row.entrypoint}</dd><dt>{t('信任边界')}</dt><dd>{detail.row.trust_boundary}</dd></dl><h3>{t('攻击前提')}</h3><ul>{detail.row.preconditions?.map(p => <li key={p}>{p}</li>)}</ul><h3>{t('影响')}</h3><p>{detail.row.impact}</p></>}
        {detail.phase === 'requirements' && <Button onClick={() => open(detail.row, 'implementation')}>{t('查看实现情况')}</Button>}
        {detail.phase === 'requirements' && <><h3>{t('需求复核')}</h3>{requirementReviewTag(detail.row.requirement_review_status)}{detail.row.requirement_reviews?.map(r => <p key={r.id}>{r.rationale}</p>)}</>}
        {detail.phase === 'implementation' && <><p>{detail.row.statement}</p>{statusTag(detail.row.implementation_status || 'NOT_CHECKED')}
          <Button type="link" onClick={() => { const req = requirements.find(r => r.id === detail.row.id); if (req) open(req, 'requirements'); }}>{t('查看需求来源')}</Button>
          <h3>{t('验收项检查')}</h3>{detail.row.criterion_checks?.map((check, index) => <section className="criterion-check" key={index}><h4>{check.acceptance_criterion}</h4>{statusTag(check.implementation_status || 'NOT_CHECKED')}<p>{check.rationale || t('尚未检查')}</p>{check.entrypoint && <p>{t('检查入口')}：{check.entrypoint}</p>}</section>)}
          {!!detail.row.finding_ids?.length && <><h3>{t('关联 Findings')}</h3>{findings.filter(f => detail.row.finding_ids!.includes(f.id)).map(f => <button className="text-link source-location" key={f.id} onClick={() => open(f, 'vulnerabilities')}>{f.title}</button>)}</>}
        </>}
        {detail.phase === 'vulnerabilities' && <>{statusTag(detail.row.severity || 'UNKNOWN')}<h3>{t('影响')}</h3><p>{detail.row.impact}</p>{!!detail.row.attack_preconditions?.length && <><h3>{t('触发条件')}</h3><ul>{detail.row.attack_preconditions.map(p => <li key={p}>{p}</li>)}</ul></>}<h3>{t('问题原因')}</h3><p>{detail.row.rationale}</p><h3>{t('修复建议')}</h3><p>{detail.row.recommendation}</p><h3>{t('相关代码')}</h3>{!locations(detail.row, true).length && <p>{t('未定位到代码')}</p>}</>}
        {detailSources.map(s => <section className="source-block" key={s.id}><h3>{sourceTitle(s)}</h3><pre>{s.content}</pre></section>)}
      </>}
    </Drawer>
  </div></ConfigProvider>;
}
