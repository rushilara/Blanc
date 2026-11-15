"use client";

import { useId, useState } from "react";
import type { RetrievalCoverage } from "@/lib/analysis";
import { TRADEOFFS, budgetNote, offerCopy, skippedLines, stillLeftOutCopy, windowLabel } from "@/lib/evidence-window";

/** The "Want more evidence?" choice, plus the notes for when more is not offered. */
export function EvidenceWindowNotice({ coverage, busy, onExpand }: {
  coverage: RetrievalCoverage | undefined;
  busy: boolean;
  onExpand: () => void;
}) {
  const [declined, setDeclined] = useState(false);
  // Set when this notice's Yes started the busy run, so progress shows here, next to
  // the button, rather than only at the top of the report.
  const [requested, setRequested] = useState(false);
  const reading = busy && requested;
  const offer = offerCopy(coverage);
  const still = stillLeftOutCopy(coverage);
  const budget = budgetNote(coverage);
  const skipped = skippedLines(coverage);
  const showOffer = offer !== null && !declined;
  if (!showOffer && !still && !budget && skipped.length === 0) return null;

  return <section className={`blanc-evidence-offer${!showOffer && !still && !budget ? " is-details-only" : ""}`} aria-label="Evidence window">
    {showOffer && offer && <form onSubmit={event => { event.preventDefault(); setRequested(true); onExpand(); }}>
      <h2>{offer}</h2>
      <ul>
        {TRADEOFFS.map(([title, detail]) => <li key={title}><strong>{title}</strong> {detail}</li>)}
      </ul>
      {reading
        ? <p className="blanc-evidence-progress" role="status"><span aria-hidden="true" />Reading more evidence and rewriting the answer… this usually takes under a minute. The new answer will appear at the top.</p>
        : <div className="blanc-evidence-actions">
          <button type="submit" className="blanc-evidence-yes" disabled={busy}>Yes</button>
          <button type="button" className="blanc-evidence-no" disabled={busy} onClick={() => setDeclined(true)}>No</button>
        </div>}
    </form>}
    {still && <p>{still}</p>}
    {budget && <p>{budget}</p>}
    {skipped.length > 0 && <details className="blanc-skipped-details">
      <summary>Evidence coverage · {skipped.length} record{skipped.length === 1 ? "" : "s"} not included</summary>
      <ul className="blanc-skipped" aria-label="Records left out of this answer">
        {skipped.map(item => <li key={`${item.recordId}-${item.rank}`}>{item.recordId} · rank {item.rank}</li>)}
      </ul>
    </details>}
  </section>;
}

/** On an expanded revision, a follow-up can stay expanded or step back to standard. */
export function FollowUpWindowChoice({ coverage, value, onChange }: {
  coverage: RetrievalCoverage | undefined;
  value: "inherit" | "standard";
  onChange: (value: "inherit" | "standard") => void;
}) {
  const name = useId();
  if (coverage?.evidence_window !== "expanded") return null;
  const current = windowLabel(coverage.evidence_window, coverage.max_records);
  return <fieldset className="blanc-followup-window">
    <legend>Evidence for this follow-up:</legend>
    <label><input type="radio" name={name} checked={value === "inherit"} onChange={() => onChange("inherit")} /> {current}</label>
    <label><input type="radio" name={name} checked={value === "standard"} onChange={() => onChange("standard")} /> Standard window</label>
  </fieldset>;
}
