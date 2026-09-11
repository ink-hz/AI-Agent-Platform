import { platformPath } from '../../auth';
export function navigateIntelligence(changes: Record<string, string | null>) {
  const query = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(changes)) {
    if (value) query.set(key, value); else query.delete(key);
  }
  history.pushState({}, '', `${platformPath('/hr/panorama')}?${query}`);
  window.dispatchEvent(new Event('platform:navigate'));
}
export function intelligenceScroller(element: HTMLElement | null): Element {
  let current = element?.parentElement;
  while (current) {
    if (/(auto|scroll)/.test(getComputedStyle(current).overflowY)) return current;
    current = current.parentElement;
  }
  return document.scrollingElement ?? document.documentElement;
}
