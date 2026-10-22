import { codeFirstLine, type Source } from './code-evidence';
import { translate, type Language } from './i18n';

export default function CodeReference({ source, language, loading, expanded, anchorId }: {
  source: Source; language: Language; loading: boolean; expanded?: boolean; anchorId?: string;
}) {
  const firstLine = codeFirstLine(source);
  return <details id={anchorId} className="code-reference" open={expanded || undefined}>
    <summary><code>{source.locator}</code></summary>
    {source.content === undefined ? <p role="status">{translate(loading ? '正在加载代码片段' : '代码片段未加载', language)}</p>
      : <pre className="numbered-code"><code>{source.content.split('\n').map((line, index) => <span className="code-line" key={index}>
        {firstLine !== undefined && <span className="code-line-number" aria-hidden="true">{firstLine + index}</span>}
        <span className="code-line-content">{line}</span>
      </span>)}</code></pre>}
  </details>;
}
