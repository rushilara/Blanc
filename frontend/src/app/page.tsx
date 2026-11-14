import { BackendStatus } from "@/components/BackendStatus";

// Placeholder workspace shell. Pane contents are filled in by later tickets
// (fixture rendering is BLA-4).
const PANES = [
  { id: "answer", title: "Answer", hint: "Source-cited answer will appear here." },
  { id: "graph", title: "Graph", hint: "Relationship graph will appear here." },
  { id: "timeline", title: "Timeline", hint: "Timeline will appear here." },
] as const;

export default function Home() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800 sm:px-6">
        <h1 className="text-lg font-semibold tracking-tight">Blanc</h1>
        <BackendStatus />
      </header>
      <main className="grid flex-1 grid-cols-1 gap-4 p-4 sm:p-6 lg:grid-cols-3">
        {PANES.map((pane) => (
          <section
            key={pane.id}
            aria-labelledby={`${pane.id}-title`}
            className="flex min-h-64 flex-col rounded-lg border border-zinc-200 dark:border-zinc-800"
          >
            <h2
              id={`${pane.id}-title`}
              className="border-b border-zinc-200 px-4 py-2 text-sm font-medium dark:border-zinc-800"
            >
              {pane.title}
            </h2>
            <div className="flex flex-1 items-center justify-center p-4 text-sm text-zinc-500">
              {pane.hint}
            </div>
          </section>
        ))}
      </main>
    </div>
  );
}
