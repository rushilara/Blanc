// Client + presentation for GET /cases/{case_id}/records/{record_id}/context: the cited
// record among the other records of its original file (the source panel's "Original
// file" view). Presentation only: evidence text is shown verbatim, never rewritten.

import type { EvidenceRecord } from "@/lib/analysis";
import { API_BASE_URL } from "@/lib/api";
import { RecordFetchError } from "@/lib/records";
import { presentSource } from "@/lib/source-presentation";

export type FileContext = {
  source_file: string;
  /** Records of this file in this case. */
  total: number;
  /** The cited record's position in the whole file; ``items[index - start]`` is it. */
  index: number;
  /** The window's offset in the whole file. */
  start: number;
  ordered_by: "time" | "locator" | "time_then_locator";
  items: EvidenceRecord[];
};

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const int = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

function isContext(value: unknown): value is FileContext {
  if (!isObject(value)) return false;
  const { source_file, total, index, start, ordered_by, items } = value;
  return typeof source_file === "string" && int(total) && int(index) && int(start)
    && (ordered_by === "time" || ordered_by === "locator" || ordered_by === "time_then_locator")
    && Array.isArray(items) && items.length > 0 && start <= index && index - start < items.length
    && items.every(item => isObject(item) && typeof item.record_id === "string"
      && typeof item.source_locator === "string" && typeof item.content === "string");
}

/** The record's file context. Throws RecordFetchError (404, network, or shape). */
export async function fetchFileContext(caseId: string, recordId: string, signal?: AbortSignal, before = 40, after = 40): Promise<FileContext> {
  let response: Response;
  try {
    response = await fetch(
      `${API_BASE_URL}/cases/${encodeURIComponent(caseId)}/records/${encodeURIComponent(recordId)}/context?before=${before}&after=${after}`,
      { cache: "no-store", signal, credentials: "include" },
    );
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new RecordFetchError("Could not reach the backend to load the original file.", null);
  }
  if (response.status === 404) throw new RecordFetchError("Record not found for this case.", 404);
  if (!response.ok) throw new RecordFetchError(`Failed to load the original file: HTTP ${response.status}`, response.status);
  let body: unknown;
  try { body = await response.json(); } catch { throw new RecordFetchError("The original file response was not valid JSON."); }
  if (!isContext(body)) throw new RecordFetchError("The original file response had an unexpected shape.");
  return body;
}

export type ContextLine =
  | { style: "chat"; who: string; text: string; time: string | null; note?: string }
  | { style: "row"; title: string; detail?: string; kind?: string; time: string | null };

const MAX_TEXT = 600;
const clip = (value: string) => (value.length > MAX_TEXT ? `${value.slice(0, MAX_TEXT - 1).trimEnd()}…` : value);
const str = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;

/** A person's name from a string or a ``{username|name|email}`` object. */
function person(value: unknown): string | null {
  if (str(value)) return value.trim();
  if (isObject(value)) for (const key of ["username", "name", "display_name", "email"]) if (str(value[key])) return (value[key] as string).trim();
  return null;
}
const SENDER_KEYS = ["sender", "from", "author", "user", "speaker", "username"];
const TEXT_KEYS = ["content", "text", "body", "message", "msg"];

/** Only a stated ISO time is shown; raw browser times stay in the raw record. */
function shownTime(record: EvidenceRecord): string | null {
  if (!record.timestamp) return null;
  const parsed = new Date(record.timestamp);
  if (Number.isNaN(parsed.getTime()) || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(record.timestamp)) return record.timestamp;
  return `${parsed.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** One line of the original-file view, for any record: a chat line when it has a sender
 *  and a message, else a row with the record's own name. */
export function contextLine(record: EvidenceRecord): ContextLine {
  const time = shownTime(record);
  const view = presentSource(record);
  if (view?.message !== undefined) {
    const who = view.fields.find(field => /sender/i.test(field.label))?.value ?? view.name;
    let note: string | undefined;
    try {
      const data = JSON.parse(record.content) as Record<string, unknown>;
      const files = Array.isArray(data.attachments) ? data.attachments.flatMap(a => (isObject(a) && str(a.filename) ? [a.filename] : [])) : [];
      if (files.length) note = `Attachment: ${files.join(", ")}`;
    } catch { /* presentSource already parsed it */ }
    return { style: "chat", who, text: clip(view.message), time, note };
  }
  if (view) {
    const detail = view.fields.find(field => field.link || /url|saved location/i.test(field.label))?.value;
    return { style: "row", title: view.name, kind: view.kind.split(" · ")[0], detail, time };
  }
  let data: unknown = null;
  try { data = JSON.parse(record.content); } catch { /* plain text */ }
  if (isObject(data)) {
    const sender = SENDER_KEYS.map(key => person(data[key])).find(Boolean);
    const body = TEXT_KEYS.map(key => data[key]).find(str);
    if (sender && body) return { style: "chat", who: sender, text: clip(body), time };
  }
  const metaSender = SENDER_KEYS.map(key => person(record.metadata?.[key])).find(Boolean);
  if (metaSender && record.modality !== "image" && record.modality !== "pdf") {
    return { style: "chat", who: metaSender, text: clip(record.content), time };
  }
  if (record.modality === "image" || record.modality === "pdf") {
    const name = record.media_path?.split("/").at(-1) ?? record.source_locator;
    return { style: "row", title: name, kind: record.modality === "pdf" ? "PDF" : "Image", time };
  }
  const firstLine = record.content.trim().split("\n")[0] ?? "";
  return { style: "row", title: clip(firstLine || "(empty record)"), detail: record.source_locator, time };
}

/** How the view says the file was ordered. */
export function orderNote(context: FileContext): string {
  if (context.ordered_by === "time") return "In recorded time order.";
  if (context.ordered_by === "locator") return "In the order of each record's position in the file.";
  return "Records with a recorded time first, in time order, then the rest by position in the file.";
}
