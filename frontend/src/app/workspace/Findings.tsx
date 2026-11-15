"use client";

import Link from "next/link";
import { presentSource } from "@/lib/source-presentation";
import { SourcePanel } from "./SourcePanel";
import { TimelineView } from "@/components/TimelineView";
import { useEffect, useRef, useSyncExternalStore, useState } from "react";
import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { citedRecordIds } from "@/lib/citations";
import { anchorCitations, splitCitations, type Anchor } from "@/lib/inline-citations";
import { analyzeCase, ApiError, listCaseRecordsPage, listCases, loadRevisionSources, type CaseSummary } from "@/lib/live-api";

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
  const [askFailed, setAskFailed] = useState(false);
  const [casesReload, setCasesReload] = useState(0);
  const [casesError, setCasesError] = useState(false);
  const request = useRef<AbortController | null>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const retryAskRef = useRef<HTMLButtonElement>(null);
  const caseTitle = cases?.find(item => item.case_id === caseId)?.title ?? caseId;

  const [feedback, setFeedback] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    listCases(controller.signal).then(items => {
      setCases(items);
      setCasesError(false);
      // import_status is not kept up to date by every import path, so it only breaks ties.
      const ready = items.filter(item => item.import_status === "ready");
      const candidates = ready.length ? ready : items;
      if (candidates.length === 1) setCaseId(candidates[0].case_id);
    }).catch(error => {
      if (!controller.signal.aborted) {
        setCases([]);
        setCasesError(true);
        setFeedback(error instanceof ApiError ? error.message : "Could not load cases. Check the backend connection.");
      }
    });
    return () => {
      controller.abort();
      request.current?.abort();
    };
  }, [casesReload]);
  const [question, setQuestion] = useState("");
  const [view, setView] = useState<(typeof views)[number]>("Findings");
  const [open, setOpen] = useState<string>();
  const sources = records;

  useEffect(() => {
    if (loading) cancelRef.current?.focus();
    else if (askFailed) retryAskRef.current?.focus();
  }, [loading, askFailed]);
  // Record IDs the model wrote into its prose become numbered citations too. Numbering
  // follows citation order, then any loaded source mentioned only in prose.
  const knownIds = revision ? [...citedRecordIds(revision), ...records.map(record => record.record_id)] : [];
  const prose = (text: string) => splitCitations(text, knownIds);
  const proseIds = revision
    ? [revision.answer, ...revision.claims.map(claim => claim.text), ...revision.unresolved_questions, ...revision.graph.edges.map(edge => edge.description ?? "")]
      .flatMap(text => prose(text).flatMap(part => "ids" in part ? part.ids : []))
    : [];
  const citedOrder = [...new Set([...(revision ? citedRecordIds(revision) : []), ...proseIds])];

  // Exact words from each cited record (search phrase, file name, sender), so the
  // answer can carry a citation right where it mentions them.
  const anchors: Anchor[] = citedOrder.flatMap(id => {
    const record = records.find(item => item.record_id === id);
    const presentation = record ? presentSource(record) : null;
    return (presentation?.anchors ?? []).map(text => ({ text, ids: [id] }));
  });

  function withCitations(text: string, extra: string[] = [], anchored = false) {
    const parts = anchored ? anchorCitations(prose(text), anchors) : prose(text);
    const extraIds = [...new Set(extra)].filter(id => !parts.some(part => "ids" in part && part.ids.includes(id)));
    return <>
      {parts.map((part, index) => "ids" in part
        ? <sup key={index}>{part.ids.map(id => citation(id))}</sup>
        : <span key={index}>{part.text}</span>)}
      {extraIds.length > 0 && <> <sup>{extraIds.map(id => citation(id))}</sup></>}
    </>;
  }
  const nodes = new Map(revision?.graph.nodes.map(node => [node.id, node.label]));

  function cancelAsk() {
    request.current?.abort();
    request.current = null;
    setLoading(false);
    // Keep the question so an interrupted analysis can be retried.
    queueMicrotask(() => questionRef.current?.focus());
  }

  async function ask() {
    const prompt = question.trim();
    if (!prompt || request.current) return;
    if (!cases?.some(item => item.case_id === caseId)) {
      setAskFailed(false);
      setFeedback("Select a case before asking a question.");
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setAskFailed(false);
    setFeedback("");
    try {
      // Gate on the case actually holding records, not on import_status.
      const evidence = await listCaseRecordsPage(caseId, 0, 1, controller.signal);
      if (controller.signal.aborted) return;
      if (evidence.total === 0) {
        setFeedback("This case has no evidence yet. Import records before asking a question.");
        return;
      }
      const result = await analyzeCase(caseId, prompt, controller.signal);
      const loaded = await loadRevisionSources(result, controller.signal);
      if (controller.signal.aborted) return;
      setRecords(loaded);
      setRevision(result);
      const expected = new Set([...citedRecordIds(result), ...result.excluded_record_ids]).size;
      if (loaded.length < expected) {
        setFeedback("Some original sources could not be loaded. Open a citation to retry.");
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setAskFailed(true);
        setFeedback(error instanceof ApiError ? error.message : "Could not reach the backend. Check that it is running, then try again.");
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        if (!controller.signal.aborted) setLoading(false);
      }
    }
  }

  function citationKind(id: string) {
    const source = sources.find(record => record.record_id === id);
    return (source && presentSource(source)?.kind) ?? (source ? "Evidence record" : "Not loaded yet");
  }

  function citationTitle(id: string) {
    const source = sources.find(record => record.record_id === id);
    return source ? sourceTitle(source) : id;
  }

  // Unloaded sources still open the drawer, which fetches the original itself.
  // One line of the record's own text for the hover card: a message, URL or saved path.
  function citationSnippet(id: string) {
    const source = sources.find(record => record.record_id === id);
    if (!source) return "Not loaded yet. Open to fetch the original record.";
    const view = presentSource(source);
    const text = view?.message ?? view?.fields.find(field => /URL|Saved location/.test(field.label))?.value ?? source.content;
    return text.replace(/\s+/g, " ").slice(0, 140);
  }

  /** A numbered citation. In the Sources rail (``label`` set) it has no hover card,
   *  since the rail already shows the name and kind. */
  function citation(id: string, label?: string) {
    const number = citedOrder.indexOf(id) + 1 || citedOrder.length + 1;
    const title = citationTitle(id);
    return <span key={id} className={`blanc-citation${open === id ? " is-active" : ""}`}>
      <button type="button" onClick={() => setOpen(id)} aria-pressed={open === id}
        aria-label={label ? `Open source ${number}: ${title}` : `Source ${number}: ${title}`}>{label ?? number}</button>
      {!label && <span className="blanc-preview" aria-hidden="true">
        <small>{number} · {citationKind(id)}</small>
        <strong>{title}</strong>
        <span>{citationSnippet(id)}</span>
      </span>}
    </span>;
  }

  function stepSource(direction: -1 | 1) {
    const index = citedOrder.indexOf(open ?? "");
    const next = citedOrder[index + direction];
    if (next) setOpen(next);
  }

  return <div className="blanc-workspace">
    {/* The static sheet avoids the missing workspace styles in the CSS pipeline. */}
    {/* eslint-disable-next-line @next/next/no-css-tags */}
    <link rel="stylesheet" href="/blanc/findings.css?v=bla-52-states" precedence="blanc" />
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

    {revision ? <div className={`blanc-investigation${open ? " has-source" : ""}`}>
      <aside className="blanc-case-rail" aria-label="Current case and sources">
        <small>Current case</small>
        <h2>{caseTitle}</h2>
        <section className="blanc-rail-sources"><h3>Sources</h3><ol>{citedOrder.map((id, i) => <li key={id}><span>{i + 1}</span><span className="blanc-rail-source">{citation(id, citationTitle(id))}<small>{citationKind(id)}</small></span></li>)}</ol></section>
      </aside>
      <main className="blanc-reading blanc-report" key={revision.analysis_id}>
      <p className="blanc-meta">{caseTitle}</p>
      <h1>{revision.question}</h1>
      {feedback && <p role="status">{feedback}</p>}
      {revision.needs_review && <p>This analysis needs investigator review.</p>}
      {view === "Findings" && <>
        <h2>Finding</h2>
        <p>{revision.answer ? withCitations(revision.answer, [], true) : "No answer in this revision."}</p>
        <h2>Why</h2>
        {revision.claims.map(claim => <p key={claim.id}><StatusLabel value={claim.status} />{withCitations(claim.text, claim.evidence_ids)}</p>)}
        {revision.unresolved_questions.length > 0 && <><h2>What remains unresolved</h2>{revision.unresolved_questions.map((question, index) => <p key={`${index}-${question}`}>{withCitations(question)}</p>)}</>}
      </>}
      {view === "Connections" && <><h2>Connections</h2>{revision.graph.edges.length === 0
        ? <p>No supported relationships.</p>
        : revision.graph.edges.map(edge => <p key={edge.id}><StatusLabel value={edge.status} />{nodes.get(edge.source) ?? edge.source} → {nodes.get(edge.target) ?? edge.target}<br />{edge.description ? withCitations(edge.description, edge.evidence_ids) : withCitations(edge.type.replaceAll("_", " "), edge.evidence_ids)}</p>)}</>}
      {view === "Timeline" && <><h2>Timeline</h2><TimelineView key={revision.analysis_id} events={revision.timeline_events} caseId={revision.case_id} records={sources} excludedRecordIds={revision.excluded_record_ids} /></>}
      {view === "Revisions" && <><h2>Current revision</h2><p>{revision.analysis_id}</p><p>Excluded records: {revision.excluded_record_ids.join(", ") || "None"}</p></>}
      {view === "Findings" && <section className="blanc-ruled"><h3>Follow-ups</h3><input aria-label="Ask a follow-up" placeholder="Ask a follow-up…" disabled /><small>Follow-up analysis is not connected yet.</small></section>}
    </main>
      {open && <SourcePanel key={open} caseId={revision.case_id} recordId={open} records={sources}
        number={citedOrder.indexOf(open) + 1 || citedOrder.length} total={citedOrder.length}
        excluded={revision.excluded_record_ids.includes(open)}
        onClose={() => setOpen(undefined)} onStep={stepSource} />}
    </div> : <main className="blanc-reading blanc-entry">
      <h1 aria-label={greeting || undefined}><TypedGreeting key={greeting} text={greeting} /></h1>
      <form onSubmit={event => { event.preventDefault(); void ask(); }}>
        <textarea ref={questionRef} rows={2} required aria-label="Ask a question about a case" placeholder="Ask a question about a case…" value={question} disabled={loading} onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }} onChange={event => setQuestion(event.target.value)} />
        <button type="submit" aria-label="Ask question" disabled={loading || !question.trim()}>→</button>
      </form>
      {loading && <p role="status">Searching case records… <button type="button" ref={cancelRef} className="blanc-retry" onClick={cancelAsk}>Cancel</button></p>}
      {!loading && feedback && <p role={askFailed || casesError ? "alert" : "status"}>{feedback}{askFailed && question.trim() && <>{" "}<button type="button" ref={retryAskRef} className="blanc-retry" onClick={() => void ask()}>Retry analysis</button></>}{casesError && <>{" "}<button type="button" className="blanc-retry" onClick={() => { setCases(null); setCasesError(false); setFeedback(""); setCasesReload(n => n + 1); }}>Retry</button></>}</p>}
      <section className="blanc-case-list" aria-labelledby="blanc-case-list-heading">
        <h2 id="blanc-case-list-heading">Available cases</h2>
        {cases === null && <p role="status">Loading cases…</p>}
        {cases?.length === 0 && !casesError && <p>No cases are available.</p>}
        {cases?.map(item => <div key={item.case_id}>
          <button type="button" className="blanc-case-option" disabled={loading}
            aria-expanded={expandedCase === item.case_id}
            onClick={() => {
              setCaseId(item.case_id);
              setExpandedCase(expandedCase === item.case_id ? undefined : item.case_id);
              setAskFailed(false);
              setFeedback("");
            }}>
            <span><strong>{item.title}</strong>{caseId === item.case_id && <small>Selected</small>}</span>
            <span className="blanc-case-chevron" aria-hidden="true" />
          </button>
          {expandedCase === item.case_id && <CaseFiles caseId={item.case_id} />}
        </div>)}
      </section>
    </main>}
  </div>;
}

const RECORDS_PER_PAGE = 50;

function CaseFiles({ caseId }: { caseId: string }) {
  const [records, setRecords] = useState<EvidenceRecord[]>();
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    listCaseRecordsPage(caseId, 0, RECORDS_PER_PAGE, controller.signal).then(page => {
      setRecords(page.items);
      setTotal(page.total);
      setError("");
    }).catch(() => {
      if (!controller.signal.aborted) setError("Could not load sources.");
    });
    return () => controller.abort();
  }, [caseId, reload]);

  async function showMore() {
    if (!records || loadingMore) return;
    const controller = request.current ?? new AbortController();
    setLoadingMore(true);
    setError("");
    try {
      const page = await listCaseRecordsPage(caseId, records.length, RECORDS_PER_PAGE, controller.signal);
      if (controller.signal.aborted) return;
      setRecords(current => [...(current ?? []), ...page.items]);
      setTotal(page.total);
    } catch {
      if (!controller.signal.aborted) setError("Could not load more sources. Try again.");
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false);
    }
  }

  if (!records) {
    return error
      ? <p role="alert">{error} <button type="button" className="blanc-retry" onClick={() => { setError(""); setReload(n => n + 1); }}>Retry</button></p>
      : <p role="status">Loading sources…</p>;
  }
  if (!records.length) return <p>No evidence files in this case.</p>;

  const files = new Map<string, EvidenceRecord[]>();
  for (const record of records) {
    const entries = files.get(record.source_file) ?? [];
    entries.push(record);
    files.set(record.source_file, entries);
  }
  const remaining = Math.max(0, total - records.length);
  return <div className="blanc-case-files-wrap">
    <ul className="blanc-case-files" tabIndex={0} aria-label="Case evidence files">
      {Array.from(files, ([file, entries]) => <li key={file}>
        <details>
          <summary>{file.split(/[\\/]/).filter(Boolean).at(-1) ?? file}</summary>
          <small className="blanc-file-path">{file}</small>
          {entries.map(record => <CaseFileRecord key={record.record_id} record={record} />)}
        </details>
      </li>)}
    </ul>
    <p className="blanc-case-files-count">Showing {records.length} of {total} records</p>
    {error && <p role="alert">{error} {remaining > 0 && <button type="button" className="blanc-retry" onClick={() => void showMore()}>Retry</button>}</p>}
    {remaining > 0 && <button type="button" className="blanc-show-more" onClick={() => void showMore()} disabled={loadingMore}>
      {loadingMore ? "Loading…" : `Show ${Math.min(RECORDS_PER_PAGE, remaining)} more records`}
    </button>}
  </div>;
}

/** Renders a record's full text only once it is opened, to keep large cases light. */
function CaseFileRecord({ record }: { record: EvidenceRecord }) {
  const [open, setOpen] = useState(false);
  return <details className="blanc-case-file-record" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>View record · {record.record_id}</summary>
    {open && <>
      <small>{record.source_locator}</small>
      <pre>{record.content}</pre>
    </>}
  </details>;
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

  return <span className="blanc-typed-greeting">
    <span className="blanc-greeting-space" aria-hidden="true">{text || "\u00a0"}</span>
    <span className="blanc-greeting-ink" aria-hidden="true">{letters.slice(0, Math.max(0, count - 1)).join("")}{count > 0 && <span key={count} className="blanc-greeting-letter">{letters[count - 1]}</span>}{count < length && <span className="blanc-greeting-caret" />}</span>
  </span>;
}

function sourceTitle(record: EvidenceRecord) {
  return presentSource(record)?.name ?? (record.source_file.split(/[\\/]/).filter(Boolean).at(-1) || "Evidence record");
}

function StatusLabel({ value }: { value: string }) {
  return <span className={`blanc-status blanc-status-${value}`}>{value.replaceAll("_", " ")}</span>;
}
