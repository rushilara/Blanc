"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { AnalysisWorkspace } from "./AnalysisWorkspace";
import { VersionsDrawer } from "./VersionsDrawer";
import { SourceReferences } from "./SourceReferences";
import {
  createBoard,
  loadCaseBoard,
  otherVersionNodePinCount,
  pinnedNodeIds,
  setNodePin,
  type InvestigationBoard,
} from "@/lib/boards-api";
import { analyzeCase, ApiError, getSource, getRevision, listCases, loadRevisionSources, searchCase, type CaseSummary, type SearchResult } from "@/lib/live-api";
import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { isRetryableStatus } from "@/lib/retryable";

type RetryAction =
  | { kind: "search" | "analysis" }
  | { kind: "revision"; analysisId: string };

type SourceLoad =
  | { ok: true; record: EvidenceRecord }
  | { ok: false; retryable: boolean };

export function LiveWorkspace({ preferredCaseId = "" }: { preferredCaseId?: string }) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [caseState, setCaseState] = useState<"loading" | "ready" | "error">("loading");
  const [reload, setReload] = useState(0);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<"search" | "analysis" | "revision" | null>(null);
  const [error, setError] = useState("");
  const [retryAction, setRetryAction] = useState<RetryAction | null>(null);
  const [search, setSearch] = useState<SearchResult | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisRevision | null>(null);
  const [sources, setSources] = useState<EvidenceRecord[]>([]);
  const [pinnedEntityIds, setPinnedEntityIds] = useState<string[]>([]);
  const [otherVersionPinCount, setOtherVersionPinCount] = useState(0);
  const active = useRef<AbortController | null>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const retryRef = useRef<HTMLButtonElement>(null);
  const boardRef = useRef<InvestigationBoard | null>(null);
  const pinQueue = useRef(Promise.resolve());
  /** Bumped on case/version switches so in-flight pins never apply to the wrong view. */
  const viewEpoch = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    listCases(controller.signal).then(items => {
      setCases(items);
      setSelected(items.find(c => c.case_id === preferredCaseId)?.case_id ?? items[0]?.case_id ?? "");
      setCaseState("ready");
    }).catch(() => { if (!controller.signal.aborted) setCaseState("error"); });
    return () => controller.abort();
  }, [preferredCaseId, reload]);
  useEffect(() => () => active.current?.abort(), []);

  useEffect(() => {
    if (pending) cancelRef.current?.focus();
    else if (retryAction && error) retryRef.current?.focus();
  }, [pending, retryAction, error]);

  function beginViewChange() {
    viewEpoch.current += 1;
    active.current?.abort();
  }

  function applyBoard(next: InvestigationBoard | null, analysisId: string) {
    boardRef.current = next;
    setPinnedEntityIds(next ? pinnedNodeIds(next, analysisId) : []);
    setOtherVersionPinCount(next ? otherVersionNodePinCount(next, analysisId) : 0);
  }

  function changeCase(id: string) {
    beginViewChange();
    active.current = null;
    setSelected(id); setSearch(null); setAnalysis(null); setSources([]);
    applyBoard(null, "");
    setError(""); setRetryAction(null); setPending(null);
  }

  function cancelRequest() {
    beginViewChange();
    active.current = null;
    setPending(null);
    // Keep the question so an interrupted ask can be retried.
    queueMicrotask(() => questionRef.current?.focus());
  }

  function rememberRetry(action: RetryAction, err: unknown) {
    const status = err instanceof ApiError ? err.status : undefined;
    if (isRetryableStatus(status)) setRetryAction(action);
    else setRetryAction(null);
  }

  async function attachBoard(revision: AnalysisRevision, signal: AbortSignal, epoch: number) {
    // Read only: browsing versions must not PATCH active_analysis_id or create boards.
    const next = await loadCaseBoard(revision.case_id, signal);
    if (signal.aborted || viewEpoch.current !== epoch) return;
    applyBoard(next, revision.analysis_id);
  }

  const onPinnedEntityIdsChange = useCallback((ids: string[]) => {
    const currentAnalysis = analysis;
    const epoch = viewEpoch.current;
    const run = async () => {
      if (!currentAnalysis) {
        if (viewEpoch.current !== epoch) return;
        setPinnedEntityIds(ids);
        setOtherVersionPinCount(0);
        return;
      }
      const stillCurrent = () => viewEpoch.current === epoch;
      const caseId = currentAnalysis.case_id;
      const analysisId = currentAnalysis.analysis_id;

      let latest = boardRef.current;
      if (!latest) {
        const caseTitle = cases.find((item) => item.case_id === caseId)?.title ?? caseId;
        latest = await createBoard(caseId, `${caseTitle} board`, analysisId);
        if (!stillCurrent()) return;
        boardRef.current = latest;
      }

      const current = new Set(pinnedNodeIds(latest, analysisId));
      const next = new Set(ids);
      for (const id of next) {
        if (!stillCurrent()) return;
        if (!current.has(id)) {
          latest = await setNodePin(caseId, latest, analysisId, id, true);
          if (!stillCurrent()) return;
          boardRef.current = latest;
        }
      }
      for (const id of current) {
        if (!stillCurrent()) return;
        if (!next.has(id)) {
          latest = await setNodePin(caseId, latest, analysisId, id, false);
          if (!stillCurrent()) return;
          boardRef.current = latest;
        }
      }
      if (!stillCurrent()) return;
      applyBoard(latest, analysisId);
    };

    const queued = pinQueue.current.then(run, run);
    pinQueue.current = queued.then(
      () => undefined,
      () => undefined,
    );
    return queued;
  }, [analysis, cases]);

  async function run(mode: "search" | "analysis") {
    if (!selected || !question.trim() || pending) return;
    beginViewChange();
    const controller = new AbortController();
    active.current = controller;
    const epoch = viewEpoch.current;
    setPending(mode); setError(""); setRetryAction(null); setAnalysis(null); setSearch(null); setSources([]);
    applyBoard(null, "");
    const ask = question.trim();
    try {
      if (mode === "search") {
        const result = await searchCase(selected, ask, controller.signal);
        if (!controller.signal.aborted) setSearch(result);
      } else {
        const result = await analyzeCase(selected, ask, controller.signal);
        const records = await loadRevisionSources(result, controller.signal);
        if (!controller.signal.aborted && viewEpoch.current === epoch) {
          setAnalysis(result);
          setSources(records);
          try {
            await attachBoard(result, controller.signal, epoch);
          } catch {
            if (!controller.signal.aborted && viewEpoch.current === epoch) {
              applyBoard(null, result.analysis_id);
              setError("Analysis ready, but pinned entities could not be loaded from the board.");
            }
          }
        }
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof ApiError ? e.message : "Could not reach the backend. Check that it is running, then try again.");
        rememberRetry({ kind: mode }, e);
      }
    } finally {
      if (active.current === controller) { setPending(null); active.current = null; }
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    await run(submitter?.value === "analysis" ? "analysis" : "search");
  }

  async function openRevision(analysisId: string): Promise<boolean> {
    if (!selected || pending) return false;
    beginViewChange();
    const controller = new AbortController();
    active.current = controller;
    const epoch = viewEpoch.current;
    setPending("revision"); setError(""); setRetryAction(null);
    try {
      const revision = await getRevision(selected, analysisId, controller.signal);
      const records = await loadRevisionSources(revision, controller.signal);
      if (controller.signal.aborted || viewEpoch.current !== epoch) return false;
      setAnalysis(revision); setSources(records); setSearch(null); setQuestion(revision.question);
      try {
        await attachBoard(revision, controller.signal, epoch);
      } catch {
        if (!controller.signal.aborted && viewEpoch.current === epoch) {
          applyBoard(null, revision.analysis_id);
          setError("Version opened, but pinned entities could not be loaded from the board.");
        }
      }
      return true;
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof ApiError ? e.message : "Could not open the saved version. Check the backend connection.");
        rememberRetry({ kind: "revision", analysisId }, e);
      }
      return false;
    } finally {
      if (active.current === controller) { setPending(null); active.current = null; }
    }
  }

  async function inspect(id: string): Promise<SourceLoad> {
    const controller = active.current;
    const caseId = selected;
    try {
      const record = await getSource(caseId, id, controller?.signal);
      // A case change unmounts SearchSources; it discards a late source response.
      return { ok: true, record };
    } catch (e) {
      const status = e instanceof ApiError ? e.status : undefined;
      return { ok: false, retryable: isRetryableStatus(status) };
    }
  }

  function retryFailed() {
    if (!retryAction || pending) return;
    if (retryAction.kind === "revision") void openRevision(retryAction.analysisId);
    else void run(retryAction.kind);
  }

  return (
    <>
      <section aria-label="Live investigation" className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6">
        <div><h1 className="text-2xl font-semibold">Explore case evidence</h1><p className="mt-2 text-sm text-zinc-500">Search the selected case and inspect its original sources. Generated findings require investigator review.</p></div>
        {caseState === "loading" && <p role="status">Loading cases…</p>}
        {caseState === "error" && <div role="alert">Could not load cases. Check the backend connection. <button type="button" className="underline" onClick={() => { setCaseState("loading"); setReload(n => n + 1); }}>Retry</button></div>}
        {caseState === "ready" && !cases.length && <p role="status">No cases are available. Import evidence into the backend before searching.</p>}
        {selected && <VersionsDrawer key={selected} caseId={selected} caseTitle={cases.find(c => c.case_id === selected)?.title ?? selected}
          activeId={analysis?.analysis_id} busy={!!pending} onSelect={openRevision} />}
        {cases.length > 0 && <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2"><label htmlFor="live-case" className="block text-sm font-semibold">Case</label>
            <select id="live-case" value={selected} onChange={e => changeCase(e.target.value)} className="w-full rounded border border-zinc-400 bg-white p-3 dark:bg-zinc-900">
              {cases.map(c => <option key={c.case_id} value={c.case_id}>{c.title}</option>)}
            </select>
          </div>
          <div className="space-y-2"><label htmlFor="live-question" className="block text-sm font-semibold">Investigation question</label>
            <textarea ref={questionRef} id="live-question" required maxLength={4000} rows={3} value={question} onChange={e => setQuestion(e.target.value)} disabled={!!pending}
              className="w-full rounded border border-zinc-400 bg-white p-3 dark:bg-zinc-900" placeholder="What would you like to establish from this case’s evidence?" />
          </div>
          <div className="flex flex-wrap gap-3">
            <button type="submit" value="search" disabled={!!pending || !question.trim()} className="rounded bg-blue-700 px-5 py-3 font-semibold text-white disabled:opacity-40">{pending === "search" ? "Searching…" : "Search evidence"}</button>
            <button type="submit" value="analysis" disabled={!!pending || !question.trim()} className="rounded border border-zinc-400 px-5 py-3 font-semibold disabled:opacity-40">{pending === "analysis" ? "Generating…" : "Generate analysis"}</button>
          </div>
        </form>}
        {pending && <p role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>{pending === "search" ? "Retrieving source excerpts…" : pending === "revision" ? "Opening saved version…" : "Waiting for a validated analysis…"}</span>
          <button type="button" ref={cancelRef} className="underline" onClick={cancelRequest}>Cancel</button>
        </p>}
        {error && <div role="alert" className="space-y-2 rounded border border-red-400 p-4 text-sm [overflow-wrap:anywhere]">
          <p>{error}</p>
          {retryAction && <button type="button" ref={retryRef} className="underline" onClick={retryFailed}>Retry</button>}
        </div>}
        {search && <div className="space-y-4" aria-label="Search results">
          <h2 className="text-lg font-semibold [overflow-wrap:anywhere]">Evidence for: {search.question}</h2>
          <p className="text-sm text-zinc-500">{search.records.length} retrieved records. These excerpts are evidence, not a generated answer.</p>
          {!search.records.length && <p role="status">No records returned for this question.</p>}
          {(search.dropped_by_limit > 0 || search.dropped_by_budget > 0) && <p className="text-sm">The evidence window omitted {search.dropped_by_limit + search.dropped_by_budget} candidate records. Results do not cover the entire case.</p>}
          {search.records.map(record => <article key={record.record_id} className="space-y-3 rounded-xl border border-zinc-300 p-4 dark:border-zinc-700">
            <h3 className="break-all font-mono text-sm font-semibold">{record.record_id}</h3>
            <p className="break-all text-xs text-zinc-500">{record.source_file} · {record.source_locator}</p>
            {record.excerpts.map((excerpt, i) => <blockquote key={i} className="whitespace-pre-wrap break-words border-l-2 border-blue-500 pl-3 text-sm leading-6">{excerpt.text}{excerpt.truncated && <span className="block text-xs text-zinc-500">Excerpt shortened to fit the evidence window.</span>}</blockquote>)}
            <SearchSources key={`${selected}:${record.record_id}`} recordId={record.record_id} caseId={selected} load={inspect} />
          </article>)}
        </div>}
      </section>
      {analysis && (
        <AnalysisWorkspace
          revision={analysis}
          records={sources}
          showQuestionInput={false}
          pinnedEntityIds={pinnedEntityIds}
          onPinnedEntityIdsChange={onPinnedEntityIdsChange}
          pinPersistence="board"
          otherVersionPinCount={otherVersionPinCount}
        />
      )}
    </>
  );
}

function SearchSources({ recordId, caseId, load }: { recordId: string; caseId: string; load: (id: string) => Promise<SourceLoad> }) {
  const [record, setRecord] = useState<EvidenceRecord | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  const [retryable, setRetryable] = useState(true);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  async function tryLoad() {
    setState("loading");
    const result = await load(recordId);
    if (!mounted.current) return;
    if (result.ok) {
      setRecord(result.record);
      setState("idle");
    } else {
      setRetryable(result.retryable);
      setState("error");
    }
  }

  if (record) return <SourceReferences caseId={caseId} ids={[recordId]} records={[record]} autoOpen />;
  return <div><button type="button" disabled={state === "loading"} className="text-sm text-blue-700 underline dark:text-blue-300" onClick={() => void tryLoad()}>{state === "loading" ? "Loading original…" : "Inspect original source"}</button>
    {state === "error" && <p role="alert" className="text-sm [overflow-wrap:anywhere]">Source unavailable.{retryable && <>{" "}<button type="button" className="underline" onClick={() => void tryLoad()}>Retry</button></>}</p>}</div>;
}
