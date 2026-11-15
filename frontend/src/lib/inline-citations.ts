/**
 * Model prose sometimes names record IDs inline, e.g. `"ccleaner" (record narcos1-url-40)`
 * or `(records a and b)`. For reading, those references become numbered citations.
 *
 * Only IDs in `knownIds` (records the revision actually cites) are replaced; unknown
 * ID-like strings are left exactly as written.
 * - A parenthetical that only points at records (IDs plus a few connecting words, such
 *   as "tab URL in download record ...") is replaced whole by its citations.
 * - A parenthetical with real wording keeps its wording; only the IDs inside become
 *   citations.
 * - A bare mention outside parentheses is replaced together with a leading
 *   "record"/"records" word.
 * Leading whitespace goes with a replaced reference, so the citation sits against the
 * preceding word, and adjacent citations merge.
 */
export type ProsePart = { text: string } | { ids: string[] };

/** Wording longer than this, besides the IDs, is treated as content worth keeping. */
const POINTER_WORDING_MAX = 40;

const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function splitCitations(text: string, knownIds: Iterable<string>): ProsePart[] {
  const ids = [...new Set(knownIds)].filter(Boolean).sort((a, b) => b.length - a.length);
  if (!ids.length || !text) return [{ text }];
  const bounded = `(?<![\\w-])(?:${ids.map(escape).join("|")})(?![\\w-])`;
  const reference = new RegExp(
    `\\s*\\(([^()]*?${bounded}[^()]*?)\\)|\\s*(?:\\brecords?\\s+)?${bounded}`,
    "gi",
  );
  const known = new Map(ids.map((id) => [id.toLowerCase(), id]));
  const idsIn = (value: string) =>
    [...value.matchAll(new RegExp(bounded, "gi"))].map((m) => known.get(m[0].toLowerCase())!);

  const parts: ProsePart[] = [];
  const pushText = (value: string) => {
    if (!value) return;
    const previous = parts.at(-1);
    if (previous && "text" in previous) previous.text += value;
    else parts.push({ text: value });
  };
  const pushIds = (found: string[]) => {
    const previous = parts.at(-1);
    // A comma or semicolon that only introduced the reference goes with it.
    if (previous && "text" in previous) previous.text = previous.text.replace(/\s*[,;]\s*$/, "");
    if (previous && "ids" in previous) {
      previous.ids.push(...found.filter((id) => !previous.ids.includes(id)));
    } else {
      parts.push({ ids: [...new Set(found)] });
    }
  };

  let last = 0;
  for (const match of text.matchAll(reference)) {
    const start = match.index ?? 0;
    pushText(text.slice(last, start));
    last = start + match[0].length;
    const inner = match[1];
    if (inner === undefined) {
      pushIds(idsIn(match[0]));
      continue;
    }
    const wording = inner
      .replace(new RegExp(bounded, "gi"), "")
      .replace(/\brecords?\b|\band\b|[,;:]/gi, "")
      .replace(/\s+/g, " ")
      .trim();
    if (wording.length <= POINTER_WORDING_MAX) {
      pushIds(idsIn(inner));
    } else {
      pushText(match[0].slice(0, match[0].indexOf("(") + 1));
      for (const part of splitCitations(inner, ids)) {
        if ("ids" in part) pushIds(part.ids);
        else pushText(part.text);
      }
      pushText(")");
    }
  }
  pushText(text.slice(last));
  return parts;
}

export type Anchor = { text: string; ids: string[] };

/** Anchors shorter than this are too likely to match unrelated words. */
const MIN_ANCHOR = 3;

/**
 * Place citations right after the words they support. Each anchor is exact text from a
 * cited record (a search phrase, a file name, a sender) with the IDs of the records it
 * came from. Its first whole-word, case-insensitive occurrence in the prose gets those
 * citations, after a closing quote if one follows. Records already cited at that spot
 * are not repeated; nothing is inferred beyond exact matches.
 */
export function anchorCitations(parts: ProsePart[], anchors: Anchor[]): ProsePart[] {
  const usable = new Map<string, string[]>();
  for (const { text, ids } of anchors) {
    const key = text.trim().toLowerCase();
    if (key.length < MIN_ANCHOR || !ids.length) continue;
    usable.set(key, [...new Set([...(usable.get(key) ?? []), ...ids])]);
  }
  // Longest first, so "image steganography download" wins over a shorter overlap.
  const pending = [...usable.entries()].sort((a, b) => b[0].length - a[0].length);
  const out: ProsePart[] = [];
  const pushIds = (ids: string[]) => {
    const previous = out.at(-1);
    if (previous && "ids" in previous) previous.ids.push(...ids.filter((id) => !previous.ids.includes(id)));
    else out.push({ ids: [...ids] });
  };

  parts.forEach((part, index) => {
    if ("ids" in part) {
      pushIds(part.ids);
      return;
    }
    const taken: [number, number][] = [];
    const inserts: { at: number; ids: string[] }[] = [];
    for (let i = 0; i < pending.length; i++) {
      const [key, ids] = pending[i];
      const pattern = new RegExp(`(?<![\\w])${escape(key)}(?![\\w])`, "gi");
      for (const match of part.text.matchAll(pattern)) {
        const start = match.index ?? 0;
        let end = start + match[0].length;
        if (taken.some(([a, b]) => start < b && end > a)) continue;
        taken.push([start, end]);
        if (/["'”’`]/.test(part.text[end] ?? "")) end += 1;
        // Skip IDs the prose already cites right here.
        const next = parts[index + 1];
        const already = end === part.text.length && next && "ids" in next ? next.ids : [];
        const fresh = ids.filter((id) => !already.includes(id));
        if (fresh.length) inserts.push({ at: end, ids: fresh });
        pending.splice(i--, 1);
        break;
      }
    }
    let cursor = 0;
    for (const { at, ids } of inserts.sort((a, b) => a.at - b.at)) {
      if (at > cursor) out.push({ text: part.text.slice(cursor, at) });
      pushIds(ids);
      cursor = at;
    }
    if (cursor < part.text.length) out.push({ text: part.text.slice(cursor) });
  });
  return out;
}
