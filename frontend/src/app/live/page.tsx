import Link from "next/link";
import { BackendStatus } from "@/components/BackendStatus";
import { LiveWorkspace } from "@/components/LiveWorkspace";

export default function LivePage() {
  return <div className="min-h-screen bg-zinc-50 dark:bg-zinc-950">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-300 px-4 py-3 dark:border-zinc-800 sm:px-6">
      <Link href="/" className="text-lg font-semibold">Blanc</Link><BackendStatus />
    </header>
    <LiveWorkspace preferredCaseId={process.env.NEXT_PUBLIC_DEFAULT_CASE_ID} />
  </div>;
}
