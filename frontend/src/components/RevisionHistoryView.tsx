"use client";

import type { AnalysisRevision } from "@/lib/analysis";
import type { RevisionSummary } from "@/lib/live-api";
import { investigationRoot } from "@/lib/revision-history";
import { investigationTitle } from "@/lib/investigation-navigation";

export function revisionChange(version: RevisionSummary, parent?: RevisionSummary) {
  if (!version.parent_analysis_id) return { title: "Investigation opened", detail: "Original question and findings saved." };
  if (!parent) return { title: "Saved version", detail: "The earlier version is not loaded here." };
  const removed = version.excluded_record_ids.filter(id => !parent.excluded_record_ids.includes(id)).length;
  const restored = parent.excluded_record_ids.filter(id => !version.excluded_record_ids.includes(id)).length;
  if (removed || restored) return { title: "Evidence changed", detail: [removed ? `${removed} record${removed === 1 ? "" : "s"} excluded` : "", restored ? `${restored} record${restored === 1 ? "" : "s"} restored` : ""].filter(Boolean).join(" · ") };
  if (version.question !== parent.question) return { title: "Follow-up asked", detail: investigationTitle(version.question) };
  return { title: "Analysis saved", detail: "A new version of this investigation was recorded." };
}

export function RevisionHistoryView({ history, revision, busy, onOpen }: {
  history: RevisionSummary[]; revision: AnalysisRevision; busy: boolean; onOpen: (id: string) => void;
}) {
  const root = investigationRoot(history, revision.analysis_id);
  const versions = root ? history.filter(item => investigationRoot(history, item.analysis_id) === root).slice().sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)) : [];
  const byId = new Map(history.map(item => [item.analysis_id, item]));
  const number = new Map(versions.map((item, i) => [item.analysis_id, i + 1]));
  return <section className="blanc-revision-history" aria-label="Investigation version history">
    <div className="blanc-revision-intro"><h2>How this investigation evolved</h2><p>Saved questions and evidence changes, in the order they were recorded.</p></div>
    {versions.length === 0 && <p className="blanc-timeline-note">Version history is not available yet. The selected version is shown below.</p>}
    <ol className="blanc-version-list">{versions.map((version, index) => {
      const change = revisionChange(version, version.parent_analysis_id ? byId.get(version.parent_analysis_id) : undefined);
      const selected = version.analysis_id === revision.analysis_id;
      const parentNumber = version.parent_analysis_id ? number.get(version.parent_analysis_id) : undefined;
      return <li key={version.analysis_id} className={selected ? "is-selected" : undefined}>
        <div className="blanc-version-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</div>
        <div className="blanc-version-story"><div className="blanc-version-caption"><strong>{change.title}</strong><span>{selected ? "Viewing" : `Version ${index + 1}`}</span></div>
          <p>{change.detail}</p>
          <small>{new Date(version.created_at).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC{parentNumber ? ` · Based on version ${parentNumber}` : ""}</small>
          <details><summary>View question and version details</summary><p>{version.question}</p><code>{version.analysis_id}</code>{version.needs_review && <p>Needs investigator review.</p>}</details>
        </div>
        {!selected && <button type="button" disabled={busy} onClick={() => onOpen(version.analysis_id)}>Open version</button>}
      </li>;
    })}</ol>
  </section>;
}
