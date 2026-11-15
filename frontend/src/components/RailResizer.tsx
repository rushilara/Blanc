"use client";

import { useCallback, useRef, type KeyboardEvent, type PointerEvent } from "react";

const KEY = "blanc.railWidth";
export const RAIL_MIN = 180;
export const RAIL_MAX = 420;
export const RAIL_DEFAULT = 250;
const STEP = 16;

export const clampRail = (value: number) => Math.min(RAIL_MAX, Math.max(RAIL_MIN, Math.round(value)));

function storedWidth(): number {
  try {
    const value = Number(window.localStorage.getItem(KEY));
    return Number.isFinite(value) && value > 0 ? clampRail(value) : RAIL_DEFAULT;
  } catch {
    return RAIL_DEFAULT; // private window or blocked storage: use the default
  }
}

function saveWidth(value: number) {
  try { window.localStorage.setItem(KEY, String(value)); } catch { /* not remembered; still applied */ }
}

/**
 * A drag handle on the case rail's right border. It sets ``--blanc-rail`` on the
 * enclosing ``.blanc-investigation`` grid directly (no re-render while dragging) and
 * remembers the width in this browser. Arrow keys resize it; double-click resets it.
 */
export function RailResizer() {
  const handle = useRef<HTMLDivElement | null>(null);
  const width = useRef(RAIL_DEFAULT);
  const drag = useRef<{ x: number; start: number } | null>(null);

  const grid = () => handle.current?.closest<HTMLElement>(".blanc-investigation") ?? null;
  const apply = (value: number) => {
    width.current = value;
    grid()?.style.setProperty("--blanc-rail", `${value}px`);
    handle.current?.setAttribute("aria-valuenow", String(value));
  };

  const attach = useCallback((node: HTMLDivElement | null) => {
    handle.current = node;
    if (!node) return;
    const value = storedWidth();
    width.current = value;
    node.closest<HTMLElement>(".blanc-investigation")?.style.setProperty("--blanc-rail", `${value}px`);
    node.setAttribute("aria-valuenow", String(value));
  }, []);

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { x: event.clientX, start: width.current };
    grid()?.classList.add("is-resizing");
  }
  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    apply(clampRail(drag.current.start + event.clientX - drag.current.x));
  }
  function endDrag() {
    if (!drag.current) return;
    drag.current = null;
    grid()?.classList.remove("is-resizing");
    saveWidth(width.current);
  }
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? STEP * 3 : STEP;
    const next = event.key === "ArrowLeft" ? width.current - step
      : event.key === "ArrowRight" ? width.current + step
      : event.key === "Home" ? RAIL_MIN
      : event.key === "End" ? RAIL_MAX
      : null;
    if (next === null) return;
    event.preventDefault();
    apply(clampRail(next));
    saveWidth(width.current);
  }

  return <div ref={attach} className="blanc-rail-resizer" role="separator" aria-orientation="vertical"
    aria-label="Resize the sources column" aria-valuemin={RAIL_MIN} aria-valuemax={RAIL_MAX} tabIndex={0}
    onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag}
    onLostPointerCapture={endDrag} onKeyDown={onKeyDown}
    onDoubleClick={() => { apply(RAIL_DEFAULT); saveWidth(RAIL_DEFAULT); }} />;
}
