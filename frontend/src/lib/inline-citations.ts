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
