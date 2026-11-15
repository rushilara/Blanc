"use client";

import { useId, useMemo, useRef, useState } from "react";
import type { RevisionSummary } from "@/lib/live-api";
import { windowLabel } from "@/lib/evidence-window";
import { groupInvestigations, type InvestigationGroup } from "@/lib/investigation-navigation";

export function InvestigationNavigation({ history, ready, activeId, busy, onOpen, variant = "rail" }: {
  history: RevisionSummary[];
  ready: boolean;
  activeId?: string;
  busy: boolean;
  onOpen: (id: string) => void;
  variant?: "rail" | "recent";
}) {
  const groups = useMemo(() => groupInvestigations(history), [history]);
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);
  const [historyKey, setHistoryKey] = useState<string>();
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const selected = groups.find(group => group.key === historyKey);
  const filtered = groups.filter(group => `${group.title} ${group.question}`.toLowerCase().includes(query.toLowerCase()));
  const visible = variant === "recent" && !all ? filtered.slice(0, 3) : filtered;

  function showHistory(group: InvestigationGroup) {
    setHistoryKey(group.key);
    dialog.current?.showModal();
  }
  function openVersion(id: string) {
    dialog.current?.close();
    onOpen(id);
  }
  const date = (value: string) => new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

  return <section className={`blanc-investigations blanc-investigations-${variant}`} aria-label={variant === "recent" ? "Recent investigations" : "Saved investigations"}>
    <div className="blanc-investigations-heading">
      <h2>{variant === "recent" ? "Recent investigations" : "Investigations"}</h2>
      <span>{groups.length || ""}</span>
      {variant === "recent" && groups.length > 3 && <button type="button" onClick={() => setAll(!all)} aria-expanded={all}>{all ? "Show recent" : "View all"}</button>}
    </div>
    {!ready ? <p role="status">Loading saved investigations…</p> : groups.length === 0 ? <p className="blanc-nav-empty">Your investigations will appear here after you ask a question.</p> : <>
      {(variant === "rail" || all) && <input aria-label="Find an investigation" placeholder="Find an investigation…" value={query} onChange={event => setQuery(event.target.value)} />}
      <ul className="blanc-investigation-items">
        {visible.map(group => {
          const active = group.versions.some(version => version.analysis_id === activeId);
          return <li key={group.key} className={active ? "is-current" : undefined}>
            <button type="button" className="blanc-investigation-open" title={group.question} aria-current={active ? "page" : undefined} disabled={busy} onClick={() => onOpen(group.versions[0].analysis_id)}>
              <strong>{group.title}</strong>
              <small>{group.sessions.length} session{group.sessions.length === 1 ? "" : "s"} · {group.versions.length} version{group.versions.length === 1 ? "" : "s"}</small>
            </button>
            <button type="button" className="blanc-investigation-history" aria-label={`History for ${group.title}`} disabled={busy} onClick={() => showHistory(group)}>History</button>
          </li>;
        })}
      </ul>
      {visible.length === 0 && <p className="blanc-nav-empty">No matching investigations.</p>}
    </>}
    <dialog className="blanc-history-drawer" ref={dialog} aria-labelledby={titleId}>
      <button type="button" className="blanc-history-close" aria-label="Close investigation history" onClick={() => dialog.current?.close()}>×</button>
      <small className="blanc-nav-kicker">Saved history</small>
      <h2 id={titleId}>{selected?.title ?? "Investigation history"}</h2>
      <p className="blanc-nav-empty">Open any saved version. Each session keeps its original questions and evidence.</p>
      {selected?.sessions.map((session, index) => <section key={session.root.analysis_id} className="blanc-history-session">
        <h3>Session {index + 1} <span>{date(session.root.created_at)}</span></h3>
        {session.versions.map((version, i) => <div key={version.analysis_id} className="blanc-history-version">
          <button type="button" aria-current={version.analysis_id === activeId ? "page" : undefined} disabled={busy} onClick={() => openVersion(version.analysis_id)}>
            <span>Version {i + 1}{version.analysis_id === activeId ? " · viewing" : ""}</span>
            <small>{date(version.created_at)} · {windowLabel(version.evidence_window, version.max_records)}{version.excluded_record_ids.length ? ` · ${version.excluded_record_ids.length} excluded` : ""}{version.needs_review ? " · needs review" : ""}</small>
          </button>
          <details><summary>View original question</summary><p>{version.question}</p></details>
        </div>)}
      </section>)}
    </dialog>
  </section>;
}
