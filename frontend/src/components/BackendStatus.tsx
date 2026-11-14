"use client";

import { useEffect, useState } from "react";

import { API_BASE_URL, getHealth } from "@/lib/api";

type Status = "checking" | "ok" | "unreachable";

const LABELS: Record<Status, string> = {
  checking: "Checking backend…",
  ok: "Backend OK",
  unreachable: "Backend unreachable",
};

const DOT: Record<Status, string> = {
  checking: "bg-zinc-400",
  ok: "bg-emerald-500",
  unreachable: "bg-red-500",
};

export function BackendStatus() {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => setStatus(health.status === "ok" ? "ok" : "unreachable"))
      .catch(() => {
        if (!controller.signal.aborted) setStatus("unreachable");
      });
    return () => controller.abort();
  }, []);

  return (
    <span
      className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400"
      title={API_BASE_URL}
    >
      <span className={`h-2 w-2 rounded-full ${DOT[status]}`} aria-hidden />
      {LABELS[status]}
    </span>
  );
}
