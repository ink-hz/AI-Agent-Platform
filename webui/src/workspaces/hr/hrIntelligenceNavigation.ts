import { platformPath } from '../../auth';
export const INTELLIGENCE_BEFORE_NAVIGATION = 'hr:intelligence-before-navigation';
export type IntelligenceNavigationDetail = {previousSearch:string;nextSearch:string};
export function navigateIntelligence(changes: Record<string, string | null>) {
  const query = new URLSearchParams(window.location.search);
  for (const [key, value] of Object.entries(changes)) {
    if (value) query.set(key, value); else query.delete(key);
  }
  const nextSearch=`?${query}`;
  window.dispatchEvent(new CustomEvent<IntelligenceNavigationDetail>(INTELLIGENCE_BEFORE_NAVIGATION,{detail:{previousSearch:window.location.search,nextSearch}}));
  history.pushState({}, '', `${platformPath('/hr/panorama')}${nextSearch}`);
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
