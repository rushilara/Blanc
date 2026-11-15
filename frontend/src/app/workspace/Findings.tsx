"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { InvestigationNavigation } from "@/components/InvestigationNavigation";
import { investigationTitle } from "@/lib/investigation-navigation";
import { SpatialConnections } from "@/components/SpatialConnections";
import { presentSource } from "@/lib/source-presentation";
import { SourcePanel } from "./SourcePanel";
import { TimelineView } from "@/components/TimelineView";
import { Component, useEffect, useRef, useSyncExternalStore, useState, type ReactNode } from "react";
import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { citedRecordIds } from "@/lib/citations";
import { anchorCitations, splitCitations, type Anchor } from "@/lib/inline-citations";
import { itemFor, sameFindingNote, sourcesInOrder, supportChanged, supportLabel, type ComparisonItem, type ComparisonKind, type RevisionComparison, type SupportStatus } from "@/lib/comparison";
import { addReconstruction, createBoard, loadCaseBoard } from "@/lib/boards-api";
import { analyzeCase, ApiError, askFollowUp, excludeRecords, getComparison, getRevision, listCaseRecordsPage, listCases, listRevisions, loadRevisionSources, restoreRecords, type CaseSummary, type RevisionSummary } from "@/lib/live-api";

/** Thrown by the dynamic() catch fallback when the map chunk fails to import. */
const MAP_LOAD_ERROR = "Connection map failed to load.";

const ConnectionMap = dynamic(
  () =>
    import("./ConnectionMap")
      .then(mod => ({ default: mod.ConnectionMap }))
      .catch(() => ({
        default: function MapLoadError(): never {
          throw new Error(MAP_LOAD_ERROR);
        },
      })),
  { ssr: false, loading: () => <p className="blanc-map-hint">Drawing the connections…</p> },
);

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
  const [previous, setPrevious] = useState<AnalysisRevision | null>(null);
  const [comparison, setComparison] = useState<RevisionComparison | null>(null);
  const [showingPrevious, setShowingPrevious] = useState(false);
  const [working, setWorking] = useState("");
  const [records, setRecords] = useState<EvidenceRecord[]>([]);
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [caseId, setCaseId] = useState("");
  const [loading, setLoading] = useState(false);
  const [askFailed, setAskFailed] = useState(false);
  const [casesReload, setCasesReload] = useState(0);
  const [casesError, setCasesError] = useState(false);
  const [history, setHistory] = useState<RevisionSummary[]>([]);
  const [historyReady, setHistoryReady] = useState(false);
  const [followUp, setFollowUp] = useState("");
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
      const params = new URLSearchParams(window.location.search);
      const wanted = params.get("case");
      if (wanted && items.some(item => item.case_id === wanted)) setCaseId(wanted);
      else if (!wanted && candidates.length === 1) setCaseId(candidates[0].case_id);
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
  const [expandedSource, setExpandedSource] = useState<string>();
  const [sourceHover, setSourceHover] = useState<{ id: string; top: number; left: number } | null>(null);
  const shown = showingPrevious && previous ? previous : revision;
  const sources = records;

  useEffect(() => {
    if (loading) cancelRef.current?.focus();
    else if (askFailed) retryAskRef.current?.focus();
  }, [loading, askFailed]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const wantedCase = params.get("case");
    const wantedAnalysis = params.get("analysis");
    if (!wantedCase || !wantedAnalysis) return;
    const controller = new AbortController();
    void (async () => {
      setWorking("Opening the saved investigation…");
      try {
        const next = await getRevision(wantedCase, wantedAnalysis, controller.signal);
        if (controller.signal.aborted) return;
        const loaded = await loadRevisionSources(next, controller.signal);
        if (controller.signal.aborted) return;
        setCaseId(wantedCase);
        setRecords(loaded);
        setRevision(next);
        setPrevious(null);
        setComparison(null);
        setShowingPrevious(false);
        setView("Findings");
      } catch (error) {
        if (!controller.signal.aborted) {
          setCaseId(wantedCase);
          setFeedback(error instanceof ApiError ? error.message : "The saved investigation could not be opened.");
        }
      } finally {
        if (!controller.signal.aborted) setWorking("");
      }
    })();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!revision) return;
    const url = new URL(window.location.href);
    url.searchParams.set("case", revision.case_id);
    url.searchParams.set("analysis", revision.analysis_id);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }, [revision]);

  useEffect(() => {
    if (!caseId) return;
    const controller = new AbortController();
    listRevisions(caseId, controller.signal).then(items => {
      if (!controller.signal.aborted) {
        setHistory(items);
        setHistoryReady(true);
      }
    }).catch(() => {
      if (!controller.signal.aborted) {
        setHistory([]);
        setHistoryReady(true);
      }
    });
    return () => controller.abort();
  }, [caseId, revision?.analysis_id]);
  // Record IDs the model wrote into its prose become numbered citations too. Numbering
  // follows citation order, then any loaded source mentioned only in prose.
  const knownIds = shown ? [...citedRecordIds(shown), ...records.map(record => record.record_id)] : [];
  const prose = (text: string) => splitCitations(text, knownIds);
  const proseIds = shown
    ? [shown.answer, ...shown.claims.map(claim => claim.text), ...shown.unresolved_questions, ...shown.graph.edges.map(edge => edge.description ?? "")]
      .flatMap(text => prose(text).flatMap(part => "ids" in part ? part.ids : []))
    : [];
  const citedOrder = [...new Set([...(shown ? citedRecordIds(shown) : []), ...proseIds])];
  const sourceOrder = sourcesInOrder(citedOrder, shown?.excluded_record_ids ?? []);

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
      setPrevious(null);
      setComparison(null);
      setShowingPrevious(false);
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

  /** A numbered citation in the answer. The Sources rail lists names separately. */
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
    const index = sourceOrder.indexOf(open ?? "");
    const next = sourceOrder[index + direction];
    if (next) setOpen(next);
  }

  function wording(kind: ComparisonKind, id: string | null, side: "base" | "compared"): string | undefined {
    const rev = side === "base" ? previous : revision;
    if (!rev || !id) return undefined;
    if (kind === "claim") return rev.claims.find(claim => claim.id === id)?.text;
    if (kind === "event") return rev.timeline_events.find(event => event.id === id)?.description;
    const edge = rev.graph.edges.find(item => item.id === id);
    if (!edge) return undefined;
    const name = (nodeId: string) => rev.graph.nodes.find(node => node.id === nodeId)?.label ?? nodeId;
    return edge.description || `${name(edge.source)} → ${name(edge.target)}`;
  }

  function comparisonWording(item: ComparisonItem): string {
    return wording(item.kind, item.compared_ref_id, "compared")
      ?? wording(item.kind, item.base_ref_id, "base")
      ?? item.kind;
  }

  function supportOf(kind: ComparisonKind, id: string) {
    if (!comparison || !revision || !previous) return undefined;
    if (comparison.compared_analysis_id !== revision.analysis_id || comparison.base_analysis_id !== previous.analysis_id) return undefined;
    return itemFor(comparison.items, kind, id, showingPrevious ? "base" : "compared");
  }

  function supportMap(kind: "edge" | "event"): Record<string, SupportStatus> | undefined {
    if (!comparison || !revision || comparison.compared_analysis_id !== revision.analysis_id) return undefined;
    const key = showingPrevious ? "base_ref_id" : "compared_ref_id";
    const marks: Record<string, SupportStatus> = {};
    for (const item of comparison.items) {
      if (item.kind !== kind) continue;
      const id = item[key];
      if (id) marks[id] = item.support_status;
    }
    return marks;
  }

  const lostClaims = !showingPrevious && comparison && comparison.compared_analysis_id === revision?.analysis_id
    ? comparison.items.filter(item => item.kind === "claim" && !item.compared_ref_id)
    : [];

  function remember(loaded: EvidenceRecord[]) {
    setRecords(current => {
      const byId = new Map(current.map(record => [record.record_id, record]));
      for (const record of loaded) byId.set(record.record_id, record);
      return [...byId.values()];
    });
  }

  async function changeEvidence(action: "exclude" | "restore") {
    if (!shown || !open || working) return;
    setWorking(action === "exclude" ? "Rerunning the analysis without this record…" : "Restoring this record from an earlier revision…");
    setFeedback("");
    try {
      const child = action === "exclude"
        ? await excludeRecords(shown.case_id, shown.analysis_id, [open])
        : await restoreRecords(shown.case_id, shown.analysis_id, [open]);
      let diff: RevisionComparison | null = null;
      try {
        diff = await getComparison(child.case_id, child.analysis_id);
      } catch {
        setFeedback("The new revision is saved. Its comparison could not be loaded.");
      }
      remember(await loadRevisionSources(child));
      setPrevious(shown);
      setRevision(child);
      setComparison(diff);
      setShowingPrevious(false);
      setView("Revisions");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "The revision could not be saved.");
    } finally {
      setWorking("");
    }
  }

  function switchCase(nextId: string) {
    if (!nextId || nextId === caseId) return;
    setCaseId(nextId);
    setRevision(null);
    setPrevious(null);
    setComparison(null);
    setShowingPrevious(false);
    setRecords([]);
    setHistory([]);
    setHistoryReady(false);
    setOpen(undefined);
    setFollowUp("");
    setFeedback("");
    const url = new URL(window.location.href);
    url.searchParams.set("case", nextId);
    url.searchParams.delete("analysis");
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  }

  async function openSaved(analysisId: string) {
    if (!caseId || working) return;
    if (analysisId === revision?.analysis_id) {
      setShowingPrevious(false);
      setView("Findings");
      return;
    }
    setWorking("Opening the saved investigation…");
    setFeedback("");
    try {
      const next = await getRevision(caseId, analysisId);
      const loaded = await loadRevisionSources(next);
      let parent: AnalysisRevision | null = null;
      if (next.parent_analysis_id) {
        try {
          parent = await getRevision(caseId, next.parent_analysis_id);
        } catch {
          parent = null;
        }
      }
      let diff: RevisionComparison | null = null;
      if (parent && parent.question === next.question) {
        try {
          diff = await getComparison(caseId, next.analysis_id);
        } catch {
          diff = null;
        }
      }
      setRecords(loaded);
      setPrevious(parent);
      setRevision(next);
      setComparison(diff);
      setShowingPrevious(false);
      setOpen(undefined);
      setView("Findings");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "The saved investigation could not be opened.");
    } finally {
      setWorking("");
    }
  }

  async function submitFollowUp() {
    const prompt = followUp.trim();
    if (!shown || !prompt || working) return;
    setWorking("Running the follow-up…");
    setFeedback("");
    try {
      let boardId: string | null = null;
      try {
        boardId = (await loadCaseBoard(shown.case_id))?.board_id ?? null;
      } catch {
        boardId = null;
      }
      const child = await askFollowUp(shown.case_id, shown.analysis_id, prompt, boardId);
      setRecords(await loadRevisionSources(child));
      setPrevious(shown);
      setRevision(child);
      setComparison(null);
      setShowingPrevious(false);
      setFollowUp("");
      setView("Findings");
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "The follow-up could not be saved.");
    } finally {
      setWorking("");
    }
  }

  async function addToBoard() {
    if (!shown || working) return;
    setWorking("Adding findings to the board…");
    setFeedback("");
    try {
      let board = await loadCaseBoard(shown.case_id);
      if (!board) board = await createBoard(shown.case_id, `${caseTitle || "Investigation"} board`, shown.analysis_id);
      try {
        board = await addReconstruction(shown.case_id, board, shown.analysis_id);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 409) throw error;
        const fresh = await loadCaseBoard(shown.case_id);
        if (!fresh) throw error;
        board = await addReconstruction(shown.case_id, fresh, shown.analysis_id);
      }
      setFeedback(`Added this revision's findings to “${board.title}”.`);
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "The findings could not be added to the board.");
    } finally {
      setWorking("");
    }
  }

  async function togglePrevious() {
    if (!revision || working) return;
    if (showingPrevious) {
      setShowingPrevious(false);
      return;
    }
    if (!revision.parent_analysis_id) return;
    if (previous?.analysis_id === revision.parent_analysis_id) {
      setShowingPrevious(true);
      return;
    }
    setWorking("Opening the previous revision…");
    setFeedback("");
    try {
      const parent = await getRevision(revision.case_id, revision.parent_analysis_id);
      remember(await loadRevisionSources(parent));
      setPrevious(parent);
      setShowingPrevious(true);
      try {
        setComparison(await getComparison(revision.case_id, revision.analysis_id));
      } catch {
        setComparison(null);
      }
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "The previous revision could not be opened.");
    } finally {
      setWorking("");
    }
  }


  return <div className="blanc-workspace">
    {/* The static sheet avoids the missing workspace styles in the CSS pipeline. */}
    {/* eslint-disable-next-line @next/next/no-css-tags */}
    <link rel="stylesheet" href="/blanc/findings.css?v=follow-up-2" precedence="blanc" />
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

    {shown ? <div className={`blanc-investigation${open ? " has-source" : ""}${view === "Connections" ? " is-map" : ""}`}>
      <aside className="blanc-case-rail" aria-label="Current case, investigations, and sources">
        <label className="blanc-case-picker">
          <small>Current case</small>
          <select aria-label="Current case" value={caseId} onChange={event => switchCase(event.target.value)}>
            {!cases && <option value={caseId}>{caseTitle || "Loading cases…"}</option>}
            {cases?.map(item => <option key={item.case_id} value={item.case_id}>{item.title}</option>)}
            {cases && caseId && !cases.some(item => item.case_id === caseId) && <option value={caseId}>{caseTitle}</option>}
          </select>
        </label>
        <InvestigationNavigation key={`navigation-${caseId}`} history={history} ready={historyReady} activeId={shown.analysis_id} busy={!!working} onOpen={id => void openSaved(id)} />
        <details className="blanc-rail-sources blanc-source-disclosure"><summary>Sources <span>{sourceOrder.length}</span></summary><ol>{sourceOrder.map((id, i) => {
          const expanded = expandedSource === id;
          const excluded = shown.excluded_record_ids.includes(id);
          return <li key={id} className={[expanded ? "is-open" : "", excluded ? "is-excluded" : ""].filter(Boolean).join(" ") || undefined}
            onMouseEnter={event => {
              if (expanded) return;
              const rect = event.currentTarget.getBoundingClientRect();
              setSourceHover({ id, top: rect.top - 4, left: rect.right + 14 });
            }}
            onMouseLeave={() => setSourceHover(current => current?.id === id ? null : current)}>
            <button type="button" className={`blanc-rail-toggle${open === id ? " is-open" : ""}`} aria-expanded={expanded} aria-controls={`blanc-rail-detail-${i}`} onClick={() => { setSourceHover(null); setExpandedSource(expanded ? undefined : id); }}>
              <span>{i + 1}</span>
              <span>{citationTitle(id)}</span>
              <span className="blanc-rail-chevron" aria-hidden="true" />
            </button>
            <div id={`blanc-rail-detail-${i}`} className="blanc-rail-detail" hidden={!expanded}>
              <small>{excluded ? "Excluded · " : ""}{citationKind(id)}</small>
              <button type="button" aria-pressed={open === id} onClick={() => setOpen(open === id ? undefined : id)}>{open === id ? "Close source" : "Open source"}</button>
            </div>
          </li>;
        })}</ol></details>
      </aside>
      <main className="blanc-reading blanc-report" key={shown.analysis_id}>
      <p className="blanc-meta">{caseTitle}{showingPrevious ? " · previous revision" : ""}</p>
      <div className="blanc-investigation-heading">
        <h1>{investigationTitle(shown.question)}</h1>
        <details className="blanc-original-question"><summary>View question</summary><p>{shown.question}</p></details>
      </div>
      {(working || feedback) && <p role="status">{working || feedback}</p>}
      {shown.needs_review && <p>This analysis needs investigator review.</p>}
      {revision?.parent_analysis_id && <p className="blanc-revision-switch">
        <button type="button" onClick={() => void togglePrevious()} disabled={!!working}>
          {showingPrevious ? "Show the revision after the change" : "Show the previous revision"}
        </button>
      </p>}
      <p className="blanc-revision-switch">
        <button type="button" onClick={() => void addToBoard()} disabled={!!working}>Add findings to board</button>
      </p>
      {view === "Findings" && <>
        <h2>Finding</h2>
        <p>{shown.answer ? withCitations(shown.answer, [], true) : "No answer in this revision."}</p>
        <h2>Why</h2>
        {shown.claims.map(claim => {
          const item = supportOf("claim", claim.id);
          const note = sameFindingNote(item?.possible_matches);
          return <p key={claim.id} className={item && supportChanged(item.support_status) ? "is-changed" : undefined}>
            <StatusLabel value={claim.status} />
            {item && <span className={`blanc-support blanc-support-${item.support_status}`}>{supportLabel(item.support_status)}</span>}
            {withCitations(claim.text, claim.evidence_ids)}
            {note && <small className="blanc-possible">{note}</small>}
          </p>;
        })}
        {lostClaims.map(item => {
          const note = sameFindingNote(item.possible_matches);
          return <p key={`lost-${item.base_ref_id}`} className="is-changed">
            <span className={`blanc-support blanc-support-${item.support_status}`}>{supportLabel(item.support_status)}</span>
            {withCitations(comparisonWording(item), item.base_evidence_ids)}
            {note && <small className="blanc-possible">{note}</small>}
          </p>;
        })}
        {shown.unresolved_questions.length > 0 && <><h2>What remains unresolved</h2>{shown.unresolved_questions.map((question, index) => <p key={`${index}-${question}`}>{withCitations(question)}</p>)}</>}
      </>}
      {view === "Connections" && <>
      <SpatialConnections graph={shown.graph} excludedRecordIds={shown.excluded_record_ids} onOpenSource={setOpen} />
      <details><summary>Relationship review and comparison</summary><ConnectionMapPane
        nodes={shown.graph.nodes}
        edges={shown.graph.edges}
        records={sources}
        claims={shown.claims}
        events={shown.timeline_events}
        questions={shown.unresolved_questions}
        cite={id => citation(id)}
        edgeSupport={supportMap("edge")}
      /></details></>}
      {view === "Timeline" && <><h2>Timeline</h2><TimelineView key={shown.analysis_id} events={shown.timeline_events} caseId={shown.case_id} records={sources} excludedRecordIds={shown.excluded_record_ids} eventSupport={supportMap("event")} /></>}
      {view === "Revisions" && <>
        <h2>{showingPrevious ? "Previous revision" : "This revision"}</h2>
        <p className="blanc-meta">{shown.analysis_id}</p>
        <p>Excluded records: {shown.excluded_record_ids.length === 0 ? "None" : shown.excluded_record_ids.map(id => <button key={id} type="button" className="blanc-excluded-link" onClick={() => setOpen(id)}>{citationTitle(id)}</button>)}</p>
        {comparison && comparison.compared_analysis_id === revision?.analysis_id && <>
          <h2>What changed</h2>
          <p>An exclusion changes what this analysis can conclude. It does not change what happened.</p>
          <ul className="blanc-comparison">
            {comparison.items.map((item, index) => {
              const note = sameFindingNote(item.possible_matches);
              return <li key={`${item.kind}-${item.base_ref_id ?? "new"}-${item.compared_ref_id ?? "gone"}-${index}`} className={supportChanged(item.support_status) ? "is-changed" : undefined}>
                <span className={`blanc-support blanc-support-${item.support_status}`}>{supportLabel(item.support_status)}</span>
                <span>{comparisonWording(item)}</span>
                {item.explanation && <p>{item.explanation}</p>}
                {note && <p className="blanc-possible">{note}</p>}
              </li>;
            })}
          </ul>
        </>}
      </>}
      {view === "Findings" && <section className="blanc-ruled">
        <h3>Follow-ups</h3>
        <form className="blanc-followup" onSubmit={event => { event.preventDefault(); void submitFollowUp(); }}>
          <input aria-label="Ask a follow-up" placeholder="Ask a follow-up…" value={followUp} disabled={!!working} onChange={event => setFollowUp(event.target.value)} />
          <button type="submit" disabled={!!working || !followUp.trim()}>Ask follow-up</button>
        </form>
      </section>}
    </main>
      {open && <SourcePanel key={open} caseId={shown.case_id} recordId={open} records={sources}
        number={sourceOrder.indexOf(open) + 1 || sourceOrder.length} total={sourceOrder.length}
        excluded={shown.excluded_record_ids.includes(open)}
        busy={!!working}
        onExclude={() => void changeEvidence("exclude")}
        onRestore={() => void changeEvidence("restore")}
        onClose={() => setOpen(undefined)} onStep={stepSource} />}
    </div> : <main className="blanc-reading blanc-entry">
      <h1 aria-label={greeting || undefined}><TypedGreeting key={greeting} text={greeting} /></h1>
      <div className="blanc-home-case">
        <label htmlFor="blanc-home-case">Case</label>
        <select id="blanc-home-case" aria-label="Choose a case" value={caseId} disabled={loading || !!working || !cases?.length} onChange={event => switchCase(event.target.value)}>
          <option value="" disabled>{cases === null ? "Loading cases…" : "Select a case"}</option>
          {cases?.map(item => <option key={item.case_id} value={item.case_id}>{item.title}</option>)}
        </select>
      </div>
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
      {working && !loading && <p role="status">{working}</p>}
      {!loading && feedback && <p role={askFailed || casesError ? "alert" : "status"}>{feedback}{askFailed && question.trim() && <>{" "}<button type="button" ref={retryAskRef} className="blanc-retry" onClick={() => void ask()}>Retry analysis</button></>}{casesError && <>{" "}<button type="button" className="blanc-retry" onClick={() => { setCases(null); setCasesError(false); setFeedback(""); setCasesReload(n => n + 1); }}>Retry</button></>}</p>}
      {caseId && <InvestigationNavigation key={`navigation-${caseId}`} history={history} ready={historyReady} busy={!!working || loading} onOpen={id => void openSaved(id)} variant="recent" />}
      {caseId && <CaseEvidenceBrowser key={`evidence-${caseId}`} caseId={caseId} />}
      {cases?.length === 0 && !casesError && <p className="blanc-nav-empty">No cases are available yet.</p>}

    </main>}
    {sourceHover && sourceOrder.includes(sourceHover.id) && <div className="blanc-rail-float" style={{ top: sourceHover.top, left: sourceHover.left }}>
      <small>{citedOrder.indexOf(sourceHover.id) + 1} · {citationKind(sourceHover.id)}</small>
      <strong>{citationTitle(sourceHover.id)}</strong>
      <span>{citationSnippet(sourceHover.id)}</span>
    </div>}
  </div>;
}

function CaseEvidenceBrowser({ caseId }: { caseId: string }) {
  const [open, setOpen] = useState(false);
  return <details className="blanc-home-evidence" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Browse case evidence</summary>
    {open && <CaseFiles caseId={caseId} />}
  </details>;
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

/** next/dynamic caches a failed import, so Retry cannot recover — offer a full reload. */
function ConnectionMapPane(props: {
  nodes: AnalysisRevision["graph"]["nodes"];
  edges: AnalysisRevision["graph"]["edges"];
  records: EvidenceRecord[];
  claims: AnalysisRevision["claims"];
  events: AnalysisRevision["timeline_events"];
  questions: string[];
  cite: (id: string) => ReactNode;
  edgeSupport?: Record<string, SupportStatus>;
}) {
  const [loadFailed, setLoadFailed] = useState(false);
  if (loadFailed) {
    return <p role="alert">The connection map failed to load.{" "}
      <button type="button" className="blanc-retry" onClick={() => { window.location.reload(); }}>Reload the page</button>
    </p>;
  }
  return <MapLoadBoundary onLoadError={() => setLoadFailed(true)}>
    <ConnectionMap {...props} />
  </MapLoadBoundary>;
}

class MapLoadBoundary extends Component<
  { children: ReactNode; onLoadError: () => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    // Chunk import failures throw MAP_LOAD_ERROR; anything else that escapes the
    // map also needs a recoverable UI (ConnectionMap already soft-falls back for canvas).
    if (error.message === MAP_LOAD_ERROR || this.state.failed) this.props.onLoadError();
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}
