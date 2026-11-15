"use client";

import Link from "next/link";
import { presentSource } from "@/lib/source-presentation";
import { useEffect, useRef, useSyncExternalStore, useState } from "react";
import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { analyzeCase, getSource, listCaseRecords, listCases, type CaseSummary } from "@/lib/live-api";

const views = ["Findings", "Connections", "Timeline", "Revisions"] as const;

function subscribeToGreeting(refresh: () => void) {
  const timer = window.setInterval(refresh, 30_000);
  return () => window.clearInterval(timer);
}

function localGreeting() {
  const hour = new Date().getHours();
  const time = hour >= 5 && hour < 12 ? "Good morning" : hour >= 12 && hour < 18 ? "Good afternoon" : "Good evening";
  let username = "";
  try {
    username = sessionStorage.getItem("blanc.username") ?? "";
  } catch {}
  return username ? `${time}, ${username}.` : `${time}.`;
}

export function Findings() {
  const greeting = useSyncExternalStore(subscribeToGreeting, localGreeting, () => "");
  const [revision, setRevision] = useState<AnalysisRevision | null>(null);
  const [records, setRecords] = useState<EvidenceRecord[]>([]);
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [caseId, setCaseId] = useState("");
  const [expandedCase, setExpandedCase] = useState<string>();
  const [loading, setLoading] = useState(false);
  const request = useRef<AbortController | null>(null);
  const caseTitle = cases?.find(item => item.case_id === caseId)?.title ?? caseId;

  const [feedback, setFeedback] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    listCases(controller.signal).then(items => {
      setCases(items);
      const ready = items.filter(item => item.import_status === "ready");
      if (ready.length === 1) setCaseId(ready[0].case_id);
    }).catch(error => {
      if (!controller.signal.aborted) {
        setCases([]);
        setFeedback(error instanceof Error ? error.message : "Could not load cases.");
      }
    });
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, []);
  const [question, setQuestion] = useState("");
  const [view, setView] = useState<(typeof views)[number]>("Findings");
  const [open, setOpen] = useState<string>();
  const sources = records;
  const active = sources.find(record => record.record_id === open);
  const nodes = new Map(revision?.graph.nodes.map(node => [node.id, node.label]));

  async function ask() {
    const prompt = question.trim();
    if (!prompt || request.current) return;
    if (!cases?.some(item => item.case_id === caseId && item.import_status === "ready")) {
      setFeedback("Select a case with evidence before asking a question.");
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setFeedback("");
    try {
      const result = await analyzeCase(caseId, prompt, controller.signal);
      const cited = new Set([
        ...result.claims.flatMap(claim => claim.evidence_ids),
        ...result.graph.edges.flatMap(edge => edge.evidence_ids),
        ...result.timeline_events.flatMap(event => event.evidence_ids),
      ]);
      const results = await Promise.allSettled(
        [...cited].map(id => getSource(caseId, id, controller.signal)),
      );
      if (controller.signal.aborted) return;
      setRecords(results.flatMap(record => record.status === "fulfilled" ? [record.value] : []));
      setRevision(result);
      if (results.some(record => record.status === "rejected")) {
        setFeedback("Some original sources could not be loaded.");
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setFeedback(error instanceof Error ? error.message : "Analysis could not be completed.");
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        if (!controller.signal.aborted) setLoading(false);
      }
    }
  }

  function citation(id: string, label?: string) {
    const index = sources.findIndex(r => r.record_id === id);
    if (index === -1) return <span key={id}> {id} (unavailable)</span>;
    const source = sources[index];
    return <span key={id} className="blanc-citation">
      <button type="button" onClick={() => setOpen(id)} aria-label={label ?? `Open source ${index + 1}: ${sourceTitle(source)}`}>{label ?? index + 1}</button>
      <span className="blanc-preview" aria-hidden="true"><strong>{sourceTitle(source)}</strong><span>Open to inspect the original record.</span></span>
    </span>;
  }

  return <div className="blanc-workspace">
    {/* The static sheet avoids the missing workspace styles in the CSS pipeline. */}
    {/* eslint-disable-next-line @next/next/no-css-tags */}
    <link rel="stylesheet" href="/blanc/findings.css?v=report-reveal" precedence="blanc" />
    <header className="blanc-header">
      <Link href="/" aria-label="Blanc home"><svg width="92" height="37" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="210 168 1460 570"><defs>
    <filter id="ink-alpha" colorInterpolationFilters="sRGB">
      <feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  -.2126 -.7152 -.0722 0 1"/>
      <feComponentTransfer><feFuncA type="linear" slope="2.2" intercept="-.57"/></feComponentTransfer>
    </filter>
    <mask id="painted-ink" maskUnits="userSpaceOnUse" x="0" y="0" width="1774" height="887" style={{maskType:"alpha"}}>
      <image href="/blanc/Blanc-Painted.png" width="1774" height="887" filter="url(#ink-alpha)"/>
    </mask>
  </defs><rect x="210" y="168" width="1460" height="570" fill="#372416"/><g fill="#e0d9c8"><rect width="1774" height="887" mask="url(#painted-ink)"/><path d="M1553 640C1554 629 1563 623 1574 624C1584 622 1593 629 1594 639C1597 647 1593 654 1588 659C1583 664 1588 669 1589 675C1590 680 1586 684 1581 684C1575 684 1571 680 1573 675C1575 670 1574 666 1570 663C1559 662 1551 653 1553 640Z"/>
      <path d="M1597 661C1602 660 1603 664 1601 667C1598 669 1594 665 1597 661Z" opacity=".58"/>
    </g></svg></Link>
      {revision && <nav aria-label="Investigation views">{views.map(item =>
        <button key={item} type="button" aria-current={item === view ? "page" : undefined} onClick={() => setView(item)}>{item}</button>
      )}</nav>}
    </header>

    {revision ? <div className="blanc-investigation">
      <aside className="blanc-case-rail" aria-label="Current case and sources">
        <small>Current case</small>
        <h2>{caseTitle}</h2>
        <section className="blanc-rail-sources"><h3>Sources</h3><ol>{sources.map((source, i) => <li key={source.record_id}><span>{i + 1}</span>{citation(source.record_id, sourceTitle(source))}</li>)}</ol></section>
      </aside>
      <main className="blanc-reading blanc-report" key={revision.analysis_id}>
      <p className="blanc-meta">{caseTitle}</p>
      <h1>{revision.question}</h1>
      {feedback && <p role="status">{feedback}</p>}
      {revision.needs_review && <p>This analysis needs investigator review.</p>}
      {view === "Findings" && <>
        <h2>Finding</h2>
        <p>{revision.answer || "No answer in this revision."}</p>
        <h2>Why</h2>
        {revision.claims.map(claim => <p key={claim.id}>{claim.text} <sup>{claim.evidence_ids.map(id => citation(id))}</sup></p>)}
        {revision.unresolved_questions.length > 0 && <><h2>What remains unresolved</h2>{revision.unresolved_questions.map(question => <p key={question}>{question}</p>)}</>}
      </>}
      {view === "Connections" && <><h2>Connections</h2>{revision.graph.edges.map(edge => <p key={edge.id}>{nodes.get(edge.source) ?? edge.source} → {nodes.get(edge.target) ?? edge.target}<br />{edge.description ?? edge.type.replaceAll("_", " ")} <sup>{edge.evidence_ids.map(id => citation(id))}</sup></p>)}</>}
      {view === "Timeline" && <><h2>Timeline</h2>{revision.timeline_events.map(event => <p key={event.id}><small>{event.timestamp ?? "Undated"}</small><br />{event.description} <sup>{event.evidence_ids.map(id => citation(id))}</sup></p>)}</>}
      {view === "Revisions" && <><h2>Current revision</h2><p>{revision.analysis_id}</p><p>Excluded records: {revision.excluded_record_ids.join(", ") || "None"}</p></>}
      {active && <EvidenceViewer key={active.record_id} record={active} onClose={() => setOpen(undefined)} />}
      {view === "Findings" && <section className="blanc-ruled"><h3>Follow-ups</h3><input aria-label="Ask a follow-up" placeholder="Ask a follow-up…" disabled /><small>Follow-up analysis is not connected yet.</small></section>}
    </main></div> : <main className="blanc-reading blanc-entry">
      <h1><TypedGreeting key={greeting} text={greeting} /></h1>
      <form onSubmit={event => { event.preventDefault(); void ask(); }}>
        <textarea rows={2} required aria-label="Ask a question about a case" placeholder="Ask a question about a case…" value={question} onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }} onChange={event => setQuestion(event.target.value)} />
        <button type="submit" aria-label="Ask question" disabled={loading || !question.trim()}>→</button>
      </form>
      {(loading || feedback) && <p role="status">{loading ? "Searching case records…" : feedback}</p>}
      <section className="blanc-case-list" aria-labelledby="blanc-case-list-heading">
        <h2 id="blanc-case-list-heading">Available cases</h2>
        {cases === null && <p role="status">Loading cases…</p>}
        {cases?.length === 0 && !feedback && <p>No cases are available.</p>}
        {cases?.map(item => <div key={item.case_id}>
          <button type="button" className="blanc-case-option" disabled={loading}
            aria-expanded={expandedCase === item.case_id}
            onClick={() => {
              setCaseId(item.case_id);
              setExpandedCase(expandedCase === item.case_id ? undefined : item.case_id);
              setFeedback("");
            }}>
            <span><strong>{item.title}</strong>{(caseId === item.case_id || item.import_status === "empty") && (
              <small>{caseId === item.case_id ? "Selected" : "No evidence files yet"}</small>
            )}</span>
            <span className="blanc-case-chevron" aria-hidden="true" />
          </button>
          {expandedCase === item.case_id && <CaseFiles caseId={item.case_id} />}
        </div>)}
      </section>
    </main>}
  </div>;
}

function CaseFiles({ caseId }: { caseId: string }) {
  const [records, setRecords] = useState<EvidenceRecord[]>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    listCaseRecords(caseId, controller.signal).then(setRecords).catch(() => {
      if (!controller.signal.aborted) setError("Could not load sources. Reopen the case to retry.");
    });
    return () => controller.abort();
  }, [caseId]);
  if (error) return <p role="status">{error}</p>;
  if (!records) return <p role="status">Loading sources…</p>;
  if (!records.length) return <p>No evidence files in this case.</p>;

  const files = new Map<string, EvidenceRecord[]>();
  for (const record of records) {
    const entries = files.get(record.source_file) ?? [];
    entries.push(record);
    files.set(record.source_file, entries);
  }
  return <ul className="blanc-case-files" tabIndex={0} aria-label="Case evidence files">
    {Array.from(files, ([file, entries]) => <li key={file}>
      <details>
        <summary>{file.split(/[\\/]/).filter(Boolean).at(-1) ?? file}</summary>
        <small className="blanc-file-path">{file}</small>
        {entries.map(record => <details className="blanc-case-file-record" key={record.record_id}>
          <summary>View record · {record.record_id}</summary>
          <small>{record.source_locator}</small>
          <pre>{record.content}</pre>
        </details>)}
      </details>
    </li>)}
  </ul>;
}

function TypedGreeting({ text }: { text: string }) {
  const [count, setCount] = useState(0);
  const letters = Array.from(text);
  const length = letters.length;

  useEffect(() => {
    if (!length) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let next = 0;
    const timer = window.setInterval(() => {
      next = motion.matches ? length : next + 1;
      setCount(next);
      if (next >= length) window.clearInterval(timer);
    }, motion.matches ? 0 : 60);
    return () => window.clearInterval(timer);
  }, [length]);

  return <span className="blanc-typed-greeting" aria-label={text}>
    <span className="blanc-greeting-space" aria-hidden="true">{text || "\u00a0"}</span>
    <span className="blanc-greeting-ink" aria-hidden="true">{letters.slice(0, Math.max(0, count - 1)).join("")}{count > 0 && <span key={count} className="blanc-greeting-letter">{letters[count - 1]}</span>}{count < length && <span className="blanc-greeting-caret" />}</span>
  </span>;
}

function sourceTitle(record: EvidenceRecord) {
  return presentSource(record)?.title ?? (record.source_file.split(/[\\/]/).filter(Boolean).at(-1) || "Evidence record");
}

function EvidenceViewer({ record, onClose }: { record: EvidenceRecord; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);

  const presentation = presentSource(record);

  return <dialog ref={dialog} className="blanc-evidence-viewer" onClose={onClose} aria-labelledby="blanc-source-title">
    <button type="button" onClick={() => dialog.current?.close()}>Close</button>
    <h2 id="blanc-source-title">{sourceTitle(record)}</h2>
    {record.timestamp && <p><time dateTime={record.timestamp}>{record.timestamp}</time></p>}
    {presentation ? <>
      <dl>{presentation.fields.map(({ label, value }) => <div key={label}>
        <dt>{label}</dt><dd>{value}</dd>
      </div>)}</dl>
      {presentation.message !== undefined && <blockquote>{presentation.message || "(Empty message)"}</blockquote>}
    </> : <pre>{record.content}</pre>}
    <details>
      <summary>Original source details</summary>
      <dl><div><dt>Path</dt><dd>{record.source_file}</dd></div>
        <div><dt>Location</dt><dd>{record.source_locator}</dd></div>
        <div><dt>Record ID</dt><dd>{record.record_id}</dd></div></dl>
      {presentation && <pre>{record.content}</pre>}
    </details>
  </dialog>;
}
