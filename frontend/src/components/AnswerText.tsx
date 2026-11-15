import type { ReactNode } from "react";
import { parseAnswer, type Inline } from "@/lib/answer-markdown";

/** The model's answer with its Markdown rendered (lists, bold, italic, code), built from
 * React elements only. ``renderText`` decorates each plain run, e.g. with citations. */
export function AnswerText({ text, renderText = value => value, className }: {
  text: string;
  renderText?: (value: string) => ReactNode;
  className?: string;
}) {
  const inline = (parts: Inline[]) => parts.map((part, index) => {
    const body = renderText(part.text);
    if (part.style === "strong") return <strong key={index}>{body}</strong>;
    if (part.style === "em") return <em key={index}>{body}</em>;
    if (part.style === "code") return <code key={index}>{body}</code>;
    return <span key={index}>{body}</span>;
  });
  return <div className={className}>
    {parseAnswer(text).map((block, index) => block.type === "p"
      ? <p key={index}>{inline(block.content)}</p>
      : block.type === "ol"
        ? <ol key={index}>{block.items.map((item, i) => <li key={i}>{inline(item)}</li>)}</ol>
        : <ul key={index}>{block.items.map((item, i) => <li key={i}>{inline(item)}</li>)}</ul>)}
  </div>;
}
