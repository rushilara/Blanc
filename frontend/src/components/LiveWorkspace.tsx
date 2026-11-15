"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { AnalysisWorkspace } from "./AnalysisWorkspace";
import { VersionsDrawer } from "./VersionsDrawer";
import { SourceReferences } from "./SourceReferences";
import { analyzeCase, ApiError, getSource, getRevision, listCases, loadRevisionSources, searchCase, type CaseSummary, type SearchResult } from "@/lib/live-api";
import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";

export function LiveWorkspace({ preferredCaseId = "" }: { preferredCaseId?: string }) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [caseState, setCaseState] = useState<"loading" | "ready" | "error">("loading");
  const [reload, setReload] = useState(0);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<"search" | "analysis" | "revision" | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState<SearchResult | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisRevision | null>(null);
  const [sources, setSources] = useState<EvidenceRecord[]>([]);
  const active = useRef<AbortController | null>(null);

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

  function changeCase(id: string) {
    active.current?.abort();
    active.current = null;
    setSelected(id); setSearch(null); setAnalysis(null); setSources([]); setError(""); setPending(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !question.trim() || pending) return;
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const mode = submitter?.value === "analysis" ? "analysis" : "search";
    const controller = new AbortController();
    active.current?.abort(); active.current = controller;
    setPending(mode); setError(""); setAnalysis(null); setSearch(null); setSources([]);
    const ask = question.trim();
    try {
      if (mode === "search") {
        const result = await searchCase(selected, ask, controller.signal);
        if (!controller.signal.aborted) setSearch(result);
      } else {
        const result = await analyzeCase(selected, ask, controller.signal);
        const records = await loadRevisionSources(result, controller.signal);
        if (!controller.signal.aborted) {
          setAnalysis(result);
          setSources(records);
        }
      }
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not reach the backend. Check that it is running, then try again.");
    } finally {
      if (active.current === controller) { setPending(null); active.current = null; }
    }
  }

  async function openRevision(analysisId: string): Promise<boolean> {
    if (!selected || pending) return false;
    const controller = new AbortController();
    active.current?.abort(); active.current = controller;
    setPending("revision"); setError("");
    try {
      const revision = await getRevision(selected, analysisId, controller.signal);
      const records = await loadRevisionSources(revision, controller.signal);
      if (controller.signal.aborted) return false;
      setAnalysis(revision); setSources(records); setSearch(null); setQuestion(revision.question);
      return true;
    } catch (e) {
      if (!controller.signal.aborted) setError(e instanceof ApiError ? e.message : "Could not open the saved version. Check the backend connection.");
      return false;
    } finally {
      if (active.current === controller) { setPending(null); active.current = null; }
    }
  }

  async function inspect(id: string) {
    const controller = active.current;
    const caseId = selected;
    try {
      const record = await getSource(caseId, id, controller?.signal);
      // A case change unmounts SearchSources; it discards a late source response.
      return record;
    } catch { return null; }
  }

  return (
    <>
      <section aria-label="Live investigation" className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6">
        <div><h1 className="text-2xl font-semibold">Explore case evidence</h1><p className="mt-2 text-sm text-zinc-500">Search the selected case and inspect its original sources. Generated findings require investigator review.</p></div>
        {caseState === "loading" && <p role="status">Loading cases…</p>}
        {caseState === "error" && <div role="alert">Could not load cases. Check the backend connection. <button className="underline" onClick={() => { setCaseState("loading"); setReload(n => n + 1); }}>Retry</button></div>}
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
            <textarea id="live-question" required maxLength={4000} rows={3} value={question} onChange={e => setQuestion(e.target.value)} disabled={!!pending}
              className="w-full rounded border border-zinc-400 bg-white p-3 dark:bg-zinc-900" placeholder="What would you like to establish from this case’s evidence?" />
          </div>
          <div className="flex flex-wrap gap-3">
            <button type="submit" value="search" disabled={!!pending || !question.trim()} className="rounded bg-blue-700 px-5 py-3 font-semibold text-white disabled:opacity-40">{pending === "search" ? "Searching…" : "Search evidence"}</button>
            <button type="submit" value="analysis" disabled={!!pending || !question.trim()} className="rounded border border-zinc-400 px-5 py-3 font-semibold disabled:opacity-40">{pending === "analysis" ? "Generating…" : "Generate analysis"}</button>
          </div>
        </form>}
        {pending && <p role="status">{pending === "search" ? "Retrieving source excerpts…" : pending === "revision" ? "Opening saved version…" : "Waiting for a validated analysis…"}</p>}
        {error && <p role="alert" className="rounded border border-red-400 p-4 text-sm">{error}</p>}
        {search && <div className="space-y-4" aria-label="Search results">
          <h2 className="text-lg font-semibold">Evidence for: {search.question}</h2>
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
      {analysis && <AnalysisWorkspace revision={analysis} records={sources} showQuestionInput={false} />}
    </>
  );
}

function SearchSources({ recordId, caseId, load }: { recordId: string; caseId: string; load: (id: string) => Promise<EvidenceRecord | null> }) {
  const [record, setRecord] = useState<EvidenceRecord | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  if (record) return <SourceReferences caseId={caseId} ids={[recordId]} records={[record]} autoOpen />;
  return <div><button type="button" disabled={state === "loading"} className="text-sm text-blue-700 underline dark:text-blue-300" onClick={async () => {
    setState("loading"); const source = await load(recordId);
    if (!mounted.current) return;
    if (source) { setRecord(source); setState("idle"); } else setState("error");
  }}>{state === "loading" ? "Loading original…" : "Inspect original source"}</button>
    {state === "error" && <p role="alert" className="text-sm">Source unavailable. Try loading it again.</p>}</div>;
}
