import type { EvidenceRecord } from "./analysis";
import { API_BASE_URL } from "./api";

const IMAGE = /\.(png|jpe?g|gif|webp)$/i;
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function parse(record: EvidenceRecord): unknown {
  try { return JSON.parse(record.content); } catch { return null; }
}

const baseName = (path: string) => path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;

/**
 * The file name of an image this record points at on the original machine (a browser
 * download's saved file, or the record's own source file), or null. The backend decides
 * whether that file is available locally; this only says whether to ask.
 */
export function localImageName(record: EvidenceRecord): string | null {
  const data = parse(record);
  if (object(data) && typeof data.target_path === "string" && IMAGE.test(data.target_path)) {
    return baseName(data.target_path);
  }
  return IMAGE.test(record.source_file) ? baseName(record.source_file) : null;
}

/** Image attachments that exist only on a remote service (e.g. Discord's CDN). Their
 *  URLs are never loaded: evidence must not trigger network calls. */
export function remoteImageAttachments(record: EvidenceRecord): { filename: string; host: string }[] {
  const data = parse(record);
  if (!object(data) || !Array.isArray(data.attachments)) return [];
  return data.attachments.flatMap(item => {
    if (!object(item) || typeof item.filename !== "string" || !IMAGE.test(item.filename)) return [];
    let host = "a remote service";
    if (typeof item.url === "string") { try { host = new URL(item.url).hostname; } catch { /* keep default */ } }
    return [{ filename: item.filename, host }];
  });
}

/** The case-scoped endpoint that serves a record's original image from local evidence. */
export function mediaUrl(caseId: string, recordId: string): string {
  return `${API_BASE_URL}/cases/${encodeURIComponent(caseId)}/records/${encodeURIComponent(recordId)}/media`;
}
