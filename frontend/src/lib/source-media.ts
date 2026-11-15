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
  // An image Blanc imported itself (BLA-68/70), e.g. an email attachment: the file is
  // served from the case folder. Its source_file is the .eml, so check modality first.
  if (record.modality === "image") return importedName(record);
  const data = parse(record);
  if (object(data) && typeof data.target_path === "string" && IMAGE.test(data.target_path)) {
    return baseName(data.target_path);
  }
  return IMAGE.test(record.source_file) ? baseName(record.source_file) : null;
}

/** The name of an imported image or PDF: the attachment filename, else the source file. */
export function importedName(record: EvidenceRecord): string {
  const name = record.metadata?.filename;
  return typeof name === "string" && name ? baseName(name) : baseName(record.source_file);
}

/** A PDF Blanc imported (BLA-70): shown as a label with a download link, never inline. */
export function importedPdfName(record: EvidenceRecord): string | null {
  return record.modality === "pdf" ? importedName(record) : null;
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

export type MediaLoadResult =
  | { status: "missing" }
  | { status: "integrity" }
  | { status: "ready"; hashVerified: boolean };

/** Map a /media HTTP status (+ optional X-Hash-Verified) for the source panel. */
export function interpretMediaResponse(
  status: number,
  headers: { get(name: string): string | null },
): MediaLoadResult {
  if (status === 404) return { status: "missing" };
  if (status === 409) return { status: "integrity" };
  if (status !== 200) return { status: "missing" };
  return { status: "ready", hashVerified: headers.get("X-Hash-Verified") === "true" };
}

/** Default caption: verified vs path-only (hash not checked). */
export function mediaCaption(name: string, hashVerified: boolean): string {
  return hashVerified
    ? `${name} · original file from local evidence`
    : `${name} · local evidence file (hash not checked)`;
}
