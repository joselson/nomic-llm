// Minimal DOM helpers. All user-provided text goes through textContent (never innerHTML).

type Child = Node | string | number | null | undefined | false;
type Props = {
  class?: string;
  style?: Partial<CSSStyleDeclaration> | string;
  title?: string;
  onclick?: (e: MouseEvent) => void;
  [k: string]: unknown;
};

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K, props: Props | null = null, ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = String(v);
      else if (k === "style") {
        if (typeof v === "string") el.setAttribute("style", v);
        else Object.assign(el.style, v);
      } else if (k.startsWith("on") && typeof v === "function") {
        el.addEventListener(k.slice(2), v as EventListener);
      } else if (k in el && typeof v !== "string") {
        (el as any)[k] = v;
      } else {
        el.setAttribute(k, v === true ? "" : String(v));
      }
    }
  }
  append(el, ...children);
  return el;
}

export function append(el: HTMLElement, ...children: Child[]) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el: HTMLElement) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

export function uiRoot(): HTMLElement {
  return document.getElementById("ui")!;
}

/** Mount an element into the overlay; returns a remover. */
export function mount(el: HTMLElement): () => void {
  uiRoot().append(el);
  return () => el.remove();
}

export function isTyping(): boolean {
  const a = document.activeElement;
  return !!a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || (a as HTMLElement).isContentEditable);
}
