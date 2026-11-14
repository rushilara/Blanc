"use client";

import { useState, type FormEvent } from "react";

export function QuestionInput({ question, onAsk }: {
  question: string;
  onAsk?: (question: string) => Promise<void>;
}) {
  const [draft, setDraft] = useState(question);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!onAsk || pending || !draft.trim()) return;
    setPending(true);
    setError("");
    try {
      await onAsk(draft.trim());
    } catch {
      setError("The question could not be submitted. Please try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2">
      <label htmlFor="investigation-question" className="text-sm font-medium">Investigation question</label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input id="investigation-question" value={draft} onChange={(event) => setDraft(event.target.value)}
          aria-describedby="question-help" disabled={pending} required
          className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-3 text-base focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-zinc-700 dark:bg-zinc-900" />
        <button type="submit" disabled={!onAsk || pending || !draft.trim()}
          className="rounded-lg bg-zinc-900 px-5 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 dark:bg-zinc-100 dark:text-zinc-900">
          {pending ? "Asking…" : "Ask question"}
        </button>
      </div>
      <p id="question-help" className="text-xs text-zinc-500">
        {onAsk ? "Results below show the saved analysis until a new answer is returned." : "Preview mode: the saved analysis is shown below. New questions cannot be submitted yet."}
      </p>
      {error && <p role="alert" className="text-sm text-red-700 dark:text-red-300">{error}</p>}
    </form>
  );
}
