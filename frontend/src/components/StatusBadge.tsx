const STATUS_STYLES: Record<string, string> = {
  observed: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  reported: "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200",
  inferred: "border-violet-300 bg-violet-50 text-violet-900 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-200",
  unresolved: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200",
};

/** Finding status badge shared by claims and relationships. */
export function Status({ value }: { value: string }) {
  return <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-semibold capitalize ${STATUS_STYLES[value] ?? "border-zinc-300 text-zinc-600"}`}>{value.replaceAll("_", " ")}</span>;
}
