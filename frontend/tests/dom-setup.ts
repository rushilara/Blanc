import { JSDOM } from "jsdom";

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});

const { window } = dom;

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return false;
    },
  }),
});

Object.defineProperties(globalThis, {
  window: { value: window, configurable: true },
  self: { value: window, configurable: true },
  document: { value: window.document, configurable: true },
  navigator: { value: window.navigator, configurable: true },
  HTMLElement: { value: window.HTMLElement, configurable: true },
  HTMLButtonElement: { value: window.HTMLButtonElement, configurable: true },
  HTMLTextAreaElement: { value: window.HTMLTextAreaElement, configurable: true },
  HTMLInputElement: { value: window.HTMLInputElement, configurable: true },
  Node: { value: window.Node, configurable: true },
  MutationObserver: { value: window.MutationObserver, configurable: true },
  Event: { value: window.Event, configurable: true },
  MouseEvent: { value: window.MouseEvent, configurable: true },
  KeyboardEvent: { value: window.KeyboardEvent, configurable: true },
  CustomEvent: { value: window.CustomEvent, configurable: true },
  Element: { value: window.Element, configurable: true },
  DocumentFragment: { value: window.DocumentFragment, configurable: true },
  TextEncoder: { value: window.TextEncoder, configurable: true },
  TextDecoder: { value: window.TextDecoder, configurable: true },
  getComputedStyle: { value: window.getComputedStyle.bind(window), configurable: true },
  requestAnimationFrame: {
    value: (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 0) as unknown as number,
    configurable: true,
  },
  cancelAnimationFrame: {
    value: (id: number) => clearTimeout(id),
    configurable: true,
  },
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
