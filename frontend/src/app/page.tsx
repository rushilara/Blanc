import fixture from "../../../fixtures/synthetic_analysis_revision.json";

import { AnalysisWorkspace } from "@/components/AnalysisWorkspace";
import { BackendStatus } from "@/components/BackendStatus";

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col bg-zinc-50 dark:bg-zinc-950">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 bg-white px-4 py-3 dark:border-zinc-800 dark:bg-zinc-950 sm:px-6">
        <p className="text-lg font-semibold tracking-tight">Blanc</p>
        <BackendStatus />
      </header>
      <aside aria-label="Synthetic data notice" className="border-b border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100 sm:px-6">
        <strong>Synthetic example.</strong> Fictional records for UI development; these are not Narcos findings.
      </aside>
      <AnalysisWorkspace revision={fixture} />
    </div>
  );
}
