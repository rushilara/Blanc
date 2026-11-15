import type { EvidenceRecord } from "./analysis";

export type SourcePresentation = {
  /** The source family, e.g. "Browser download record". */
  title: string;
  /** The source's own name: the page's title, the downloaded file's name, or the
   *  Discord sender. Taken verbatim from the record (only shortened for display). */
  name: string;
  /** What kind of record it is, e.g. "Google search · Chrome history". */
  kind: string;
  /** Exact words from the record (a search phrase, a file name, a sender) that may
   *  appear in prose about it; used to place citations next to those words. */
  anchors: string[];
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

/** The search phrase if ``url`` is a web search, else null. */
function searchQuery(url: string): string | null {
  try {
    const parsed = new URL(url);
    const query = parsed.searchParams.get("q")?.trim();
    const engine = /(^|\.)(google|bing|duckduckgo)\./i.test(parsed.hostname) && /\/search\b|^\/$/.test(parsed.pathname);
    return engine && query ? query : null;
  } catch {
    return null;
  }
}

const host = (url: string) => { try { return new URL(url).hostname; } catch { return url; } };

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
    const file = fields[0].value;
    // A search phrase in the page the download started from is part of this record too.
    const searched = [data.tab_url, data.referrer, data.tab_referrer_url]
      .flatMap(url => (text(url) ? [searchQuery(url)] : []))
      .filter((query): query is string => !!query);
    return { title: "Browser download record", name: shorten(file), kind: "Download · Chrome history", anchors: [file, ...new Set(searched)], fields };
  }
  if (chrome && /SQLite urls\.id=/.test(record.source_locator) && text(data.url) && text(data.title)) {
    const fields = [{ label: "Page title", value: data.title }, { label: "URL", value: data.url }];
    if (count(data.visit_count)) fields.push({ label: "Recorded visits", value: String(data.visit_count) });
    const query = searchQuery(data.url);
    return {
      title: "Browser history record",
      name: shorten(data.title.trim() || host(data.url)),
      kind: query ? "Web search · Chrome history" : "Web page · Chrome history",
      anchors: query ? [query] : [],
      fields,
    };
  }
  if (/\/Discord\/Cache\//i.test(path) && /Discord message id /.test(record.source_locator)
    && object(data.author) && text(data.author.username) && text(data.content)) {
    const sender = data.author.username;
    return { title: "Discord message", name: `Message from ${shorten(sender)}`, kind: "Discord message · Discord cache", anchors: [sender], fields: [{ label: "Sender account", value: sender }], message: data.content };
  }
  return null;
}
