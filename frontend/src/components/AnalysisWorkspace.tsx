import type { ReactNode } from "react";
import type { AnalysisRevision, EvidenceRecord } from "@/lib/analysis";
import { QuestionInput } from "@/components/QuestionInput";
import { SourceReferences } from "@/components/SourceReferences";
import { TimelineView } from "@/components/TimelineView";

function Pane({ id, title, count, children }: {
  id: string; title: string; count: number; children: ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-title`} className="min-w-0 rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
        <h2 id={`${id}-title`} className="font-semibold">{title}</h2>
        <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">{count}</span>
      </div>
      <div className="space-y-5 p-5">{children}</div>
    </section>
  );
}

const STATUS_STYLES: Record<string, string> = {
  observed: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  reported: "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200",
  inferred: "border-violet-300 bg-violet-50 text-violet-900 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-200",
  unresolved: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200",
};

function Status({ value }: { value: string }) {
  return <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-semibold capitalize ${STATUS_STYLES[value] ?? "border-zinc-300 text-zinc-600"}`}>{value.replaceAll("_", " ")}</span>;
}

// Fixture loading is outside this component. BLA-42 can pass a validated live
// response to the same renderer; no case-specific findings belong here.
export function AnalysisWorkspace({ revision, records = [], showQuestionInput = true }: { revision: AnalysisRevision; records?: EvidenceRecord[]; showQuestionInput?: boolean }) {
  // Do not serialize other cases' excerpts into the client-side source controls.
  const caseRecords = records.filter((record) => record.case_id === revision.case_id);
  const nodes = new Map(revision.graph.nodes.map((node) => [node.id, node.label]));
  return (
    <main className="mx-auto w-full max-w-7xl space-y-6 p-4 sm:p-6">
      {showQuestionInput && <QuestionInput key={revision.analysis_id} question={revision.question} />}
      <div className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-widest text-zinc-500">Saved analysis</p>
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{revision.question}</h1>
        <p className="break-all font-mono text-xs text-zinc-500">Case {revision.case_id} · Revision {revision.analysis_id}</p>
      </div>
      {revision.needs_review && (
        <aside aria-label="Analysis requires review" className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
          <p className="font-semibold">This analysis needs investigator review.</p>
          {(revision.validation_errors ?? []).map((error, index) => <p key={index}>{error}</p>)}
        </aside>
      )}
      <div className="grid items-start gap-5 lg:grid-cols-3">
        <Pane id="answer" title="Answer" count={revision.claims.length}>
          <p className="text-sm leading-7">{revision.answer || "No answer in this revision."}</p>
          <ul className="space-y-4">
            {revision.claims.map((claim) => (
              <li key={claim.id} className="space-y-2 border-l-2 border-zinc-200 pl-3 dark:border-zinc-700">
                <Status value={claim.status} />
                <p className="text-sm leading-6">{claim.text}</p>
                <SourceReferences records={caseRecords} caseId={revision.case_id} ids={claim.evidence_ids} />
              </li>
            ))}
          </ul>
          {!revision.claims.length && <p className="text-sm text-zinc-500">No supported claims.</p>}
          {!!revision.unresolved_questions.length && (
            <aside className="space-y-2 rounded-lg bg-zinc-100 p-3 dark:bg-zinc-900">
              <h3 className="text-sm font-semibold">Still unresolved</h3>
              <ul className="list-disc space-y-2 pl-4 text-sm leading-6">
                {revision.unresolved_questions.map((question, index) => <li key={index}>{question}</li>)}
              </ul>
            </aside>
          )}
        </Pane>
        <Pane id="graph" title="Relationships" count={revision.graph.edges.length}>
          <ul aria-label="Entities" className="flex flex-wrap gap-2">
            {revision.graph.nodes.map((node) => (
              <li key={node.id} className="rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-700">
                <p className="text-sm font-medium">{node.label}</p>
                <p className="text-xs capitalize text-zinc-500">{node.type}</p>
              </li>
            ))}
          </ul>
          <ul aria-label="Relationships" className="space-y-4">
            {revision.graph.edges.map((edge) => (
              <li key={edge.id} className="space-y-2 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900">
                <p className="break-words text-sm font-medium">{nodes.get(edge.source) ?? edge.source} <span aria-label="to">→</span> {nodes.get(edge.target) ?? edge.target}</p>
                <p className="text-xs text-zinc-500">{edge.type.replaceAll("_", " ")}</p>
                {edge.description && <p className="text-sm leading-6">{edge.description}</p>}
                <Status value={edge.status} />
                <SourceReferences records={caseRecords} caseId={revision.case_id} ids={edge.evidence_ids} />
              </li>
            ))}
          </ul>
          {!revision.graph.edges.length && <p className="text-sm text-zinc-500">No supported relationships.</p>}
        </Pane>
        <Pane id="timeline" title="Timeline" count={revision.timeline_events.length}>
          <TimelineView key={`${revision.case_id}:${revision.analysis_id}`} events={revision.timeline_events} caseId={revision.case_id} records={caseRecords} />
        </Pane>
      </div>
      {!!revision.excluded_record_ids.length && (
        <aside className="space-y-2 text-sm text-zinc-500">
          <p>Excluded from this revision</p>
          <SourceReferences records={caseRecords} caseId={revision.case_id} ids={revision.excluded_record_ids} />
        </aside>
      )}
    </main>
  );
}
