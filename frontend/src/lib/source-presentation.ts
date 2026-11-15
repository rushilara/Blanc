import type { EvidenceRecord } from "./analysis";

export type SourcePresentation = {
  /** The source family, e.g. "Browser download record". */
  title: string;
  /** A short name that tells this record apart from others of its family, built only
   *  from the record's own fields, e.g. `Search: "ccleaner"` or `Download: setup.exe`. */
  label: string;
  fields: { label: string; value: string }[];
  message?: string;
};
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string";
const MAX_LABEL = 48;
const shorten = (value: string) => {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > MAX_LABEL ? `${flat.slice(0, MAX_LABEL - 1).trimEnd()}…` : flat;
};

/** `Search: "query"` for a web search URL, else `Page: <title or host>`. */
function historyLabel(url: string, title: string): string {
  try {
    const parsed = new URL(url);
    const query = parsed.searchParams.get("q");
    if (/(^|\.)(google|bing|duckduckgo)\./i.test(parsed.hostname) && /\/search\b|^\/$/.test(parsed.pathname) && query?.trim()) {
      return `Search: "${shorten(query)}"`;
    }
    return `Page: ${shorten(title.trim() || parsed.hostname)}`;
  } catch {
    return `Page: ${shorten(title.trim() || url)}`;
  }
}

const count = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

// Presentation only: recognize source families and shapes, never rewrite evidence or
// infer download success, execution, identity, or dates from a filename/record ID.
export function presentSource(record: EvidenceRecord): SourcePresentation | null {
  let data: unknown;
  try { data = JSON.parse(record.content); } catch { return null; }
  if (!object(data)) return null;
  const path = record.source_file.replaceAll("\\", "/");
  const chrome = /\/Google\/Chrome\/.*\/History$/i.test(path);
  if (chrome && /SQLite downloads\.id=/.test(record.source_locator)
    && text(data.target_path) && Array.isArray(data.url_chain)
    && data.url_chain.every(item => object(item) && text(item.url))) {
    const fields = [
      { label: "File name", value: data.target_path.split(/[\\/]/).pop() || data.target_path },
      { label: "Saved location", value: data.target_path },
      ...data.url_chain.map((item, index) => ({ label: `Download URL ${index + 1}`, value: item.url as string })),
    ];
    if (text(data.referrer) && data.referrer) fields.push({ label: "Referring page", value: data.referrer });
    if (count(data.received_bytes)) fields.push({ label: "Received bytes", value: data.received_bytes.toLocaleString("en-US") });
    if (count(data.total_bytes)) fields.push({ label: "Total bytes", value: data.total_bytes.toLocaleString("en-US") });
    return { title: "Browser download record", label: `Download: ${shorten(fields[0].value)}`, fields };
  }
  if (chrome && /SQLite urls\.id=/.test(record.source_locator) && text(data.url) && text(data.title)) {
    const fields = [{ label: "Page title", value: data.title }, { label: "URL", value: data.url }];
    if (count(data.visit_count)) fields.push({ label: "Recorded visits", value: String(data.visit_count) });
    return { title: "Browser history record", label: historyLabel(data.url, data.title), fields };
  }
  if (/\/Discord\/Cache\//i.test(path) && /Discord message id /.test(record.source_locator)
    && object(data.author) && text(data.author.username) && text(data.content)) {
    return { title: "Discord message", label: `Discord: ${shorten(data.author.username)}`, fields: [{ label: "Sender account", value: data.author.username }], message: data.content };
  }
  return null;
}
