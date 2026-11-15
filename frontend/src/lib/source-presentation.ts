import type { EvidenceRecord } from "./analysis";

export type PresentedField = {
  label: string;
  value: string;
  /** An http(s) address the investigator may choose to open; never loaded automatically. */
  link?: string;
  /** True for values read out of another field (e.g. parts of a URL), not stored separately. */
  derived?: boolean;
};

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
  fields: PresentedField[];
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

const decodePart = (part: string) => {
  try { return decodeURIComponent(part.replace(/\+/g, " ")).trim(); } catch { return part.trim(); }
};
const coordinate = (value: string) => Number(value).toFixed(4);

/** How much of the map a Google Maps zoom level shows (approximate, for reading). */
function zoomScale(zoom: number): string {
  if (zoom <= 5) return "country or larger";
  if (zoom <= 9) return "region";
  if (zoom <= 12) return "city";
  if (zoom <= 15) return "suburb";
  return "street";
}

/**
 * Readable parts of a Google Maps address, read only from the URL itself: the place,
 * the places in a directions request, a map search, the map centre and zoom, and the
 * place's pin. Returns no fields for anything that is not a Google Maps URL.
 */
export function mapsDetails(url: string): { fields: PresentedField[]; places: string[] } {
  let parsed: URL;
  try { parsed = new URL(url); } catch { return { fields: [], places: [] }; }
  const isMaps = /(^|\.)google\.[a-z.]+$/i.test(parsed.hostname) && parsed.pathname.startsWith("/maps")
    || /^maps\.google\./i.test(parsed.hostname);
  if (!isMaps) return { fields: [], places: [] };

  const fields: PresentedField[] = [];
  const places: string[] = [];
  const segments = parsed.pathname.split("/").filter(Boolean).slice(1);
  const words = (from: number) => {
    const out: string[] = [];
    for (const segment of segments.slice(from)) {
      if (segment.startsWith("@") || segment.startsWith("data=")) break;
      const text = decodePart(segment);
      if (text) out.push(text);
    }
    return out;
  };
  const kind = segments[0];
  if (kind === "place") {
    const [place] = words(1);
    if (place) { fields.push({ label: "Place", value: place, derived: true }); places.push(place); }
  } else if (kind === "dir") {
    const stops = words(1);
    if (stops.length) { fields.push({ label: "Directions", value: stops.join(" → "), derived: true }); places.push(...stops); }
  } else if (kind === "search") {
    const [query] = words(1);
    if (query) fields.push({ label: "Map search", value: query, derived: true });
  }

  const view = parsed.pathname.match(/@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(\d+(?:\.\d+)?)z/);
  if (view) {
    fields.push({ label: "Map centre", value: `${coordinate(view[1])}, ${coordinate(view[2])}`, derived: true });
    const zoom = Number(view[3]);
    fields.push({ label: "Zoom", value: `${view[3]} (${zoomScale(zoom)})`, derived: true });
  }
  const pin = (parsed.pathname + parsed.search).match(/!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/);
  if (pin) fields.push({ label: "Place pin", value: `${coordinate(pin[1])}, ${coordinate(pin[2])}`, derived: true });
  // "Stokes Valley, Lower Hutt 5019" is usually written "Stokes Valley" in prose.
  const short = places.map(place => place.split(",")[0].trim()).filter(part => part.length >= 4);
  return { fields, places: [...new Set([...places, ...short])] };
}

/** Chrome often saves just "Google Maps" as the title; name the visit by its place or route. */
function mapsName(title: string, fields: PresentedField[]): string | null {
  const generic = /^\s*(google maps)?\s*$/i.test(title);
  if (!generic) return null;
  const place = fields.find(field => field.label === "Place")?.value;
  if (place) return place;
  const route = fields.find(field => field.label === "Directions")?.value;
  if (route) return route.includes(" → ") ? `Route: ${route}` : `Directions to ${route}`;
  const search = fields.find(field => field.label === "Map search")?.value;
  if (search) return `Map search: ${search}`;
  return null;
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
    const maps = mapsDetails(data.url);
    const link = /^https?:\/\//i.test(data.url) ? data.url : undefined;
    const fields: PresentedField[] = [{ label: "Page title", value: data.title }, { label: "URL", value: data.url, link }, ...maps.fields];
    if (count(data.visit_count)) fields.push({ label: "Recorded visits", value: String(data.visit_count) });
    const query = searchQuery(data.url);
    return {
      title: "Browser history record",
      name: shorten(mapsName(data.title, maps.fields) ?? (data.title.trim() || host(data.url))),
      kind: query ? "Web search · Chrome history" : maps.fields.length ? "Google Maps · Chrome history" : "Web page · Chrome history",
      anchors: query ? [query] : maps.places,
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
