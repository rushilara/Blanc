// The model sometimes writes its answer in Markdown (numbered lists, **bold**, `code`),
// which showed up raw on the page. This turns that small subset into structure the UI
// renders as React elements: never HTML, so nothing in model text can inject markup.
// Anything outside the subset stays as plain text, exactly as written.

export type InlineStyle = "strong" | "em" | "code";
export type Inline = { text: string; style?: InlineStyle };
export type AnswerBlock =
  | { type: "p"; content: Inline[] }
  | { type: "ol" | "ul"; items: Inline[][] };

const ORDERED = /^\s*\d{1,3}[.)]\s+(.*)$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const HEADING = /^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$/;

/** Inline Markdown: `code`, **bold** / __bold__, and *italic*. Underscore italics are left
 * alone so file names like saved_networks.json are never mangled. */
export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  const push = (value: string, style?: InlineStyle) => {
    if (!value) return;
    const last = out.at(-1);
    if (!style && last && !last.style) last.text += value;
    else out.push(style ? { text: value, style } : { text: value });
  };
  const pattern = /`([^`\n]+)`|\*\*(?=\S)([^*\n]*?\S)\*\*|__(?=\S)([^_\n]*?\S)__|(?<![\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![\w*])/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    push(text.slice(last, start));
    if (match[1] !== undefined) push(match[1], "code");
    else if (match[2] !== undefined) push(match[2], "strong");
    else if (match[3] !== undefined) push(match[3], "strong");
    else push(match[4], "em");
    last = start + match[0].length;
  }
  push(text.slice(last));
  return out.length ? out : [{ text: "" }];
}

/** Paragraphs (blank-line separated; single newlines join with a space), numbered and
 * bulleted lists, and headings (shown as a bold paragraph). */
export function parseAnswer(text: string): AnswerBlock[] {
  const blocks: AnswerBlock[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) blocks.push({ type: "p", content: parseInline(paragraph.join(" ")) });
    paragraph = [];
  };
  const addItem = (type: "ol" | "ul", value: string) => {
    flush();
    const last = blocks.at(-1);
    if (last && last.type === type) last.items.push(parseInline(value));
    else blocks.push({ type, items: [parseInline(value)] });
  };
  for (const raw of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = raw.trim();
    let match: RegExpMatchArray | null;
    if (!line) flush();
    else if ((match = raw.match(ORDERED))) addItem("ol", match[1]);
    else if ((match = raw.match(BULLET))) addItem("ul", match[1]);
    else if ((match = raw.match(HEADING))) {
      flush();
      blocks.push({ type: "p", content: [{ text: match[1], style: "strong" }] });
    } else {
      // A wrapped line right after a list item continues that item.
      const last = blocks.at(-1);
      if (!paragraph.length && last && last.type !== "p" && /^\s/.test(raw)) {
        const item = last.items[last.items.length - 1];
        for (const part of parseInline(` ${line}`)) {
          const tail = item.at(-1);
          if (!part.style && tail && !tail.style) tail.text += part.text;
          else item.push(part);
        }
      } else paragraph.push(line);
    }
  }
  flush();
  return blocks;
}

/** The answer as plain text with the Markdown markers removed (for search and copying). */
export function plainAnswer(text: string): string {
  return parseAnswer(text)
    .map(block => block.type === "p"
      ? block.content.map(part => part.text).join("")
      : block.items.map((item, index) => `${block.type === "ol" ? `${index + 1}.` : "•"} ${item.map(part => part.text).join("")}`).join("\n"))
    .join("\n");
}
