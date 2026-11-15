import type { EvidenceRecord } from "@/lib/analysis";
import { presentSource } from "@/lib/source-presentation";

export function SourceContent({ record }: { record: EvidenceRecord }) {
  const presentation = presentSource(record);
  const original = <blockquote className="whitespace-pre-wrap [overflow-wrap:anywhere] rounded-lg bg-zinc-100 p-4 font-mono text-xs leading-6 dark:bg-zinc-900">{record.content}</blockquote>;
  if (!presentation) return <div className="space-y-2"><h3 className="text-sm font-semibold">Original excerpt</h3>{original}</div>;
  return <section aria-label="Readable source record" className="space-y-5">
    <div className="space-y-3 rounded-lg border border-zinc-300 p-4 dark:border-zinc-700">
      <h3 className="font-semibold">{presentation.title}</h3>
      <dl className="space-y-3 text-sm">
        {presentation.fields.map((field, index) => <div key={index}>
          <dt className="font-medium text-zinc-500 dark:text-zinc-400">{field.label}</dt>
          <dd className="mt-1 whitespace-pre-wrap [overflow-wrap:anywhere] leading-6">{field.value}</dd>
        </div>)}
      </dl>
      {presentation.message !== undefined && <div className="space-y-2">
        <h4 className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Message text</h4>
        <blockquote className="whitespace-pre-wrap [overflow-wrap:anywhere] border-l-2 border-blue-500 pl-3 text-sm leading-7">{presentation.message || "(Empty message)"}</blockquote>
      </div>}
      <p className="text-xs leading-5 text-zinc-500 dark:text-zinc-400">Fields extracted from the original record for readability. The complete raw record is preserved below.</p>
    </div>
    <details className="rounded-lg border border-zinc-300 p-3 dark:border-zinc-700">
      <summary className="cursor-pointer text-sm font-semibold">View raw record</summary>
      <div className="mt-3">{original}</div>
    </details>
  </section>;
}
