import { createRoot } from 'react-dom/client';
import { ArticleMarkdown } from './components/ai-notes/ArticleMarkdown';

export type ManufacturingMarkdownRenderer = (
  element: HTMLElement,
  options: { markdown: string; headingIds?: Readonly<Record<number, string>> },
) => { unmount(): void };

// Each mount owns its child and root. Retired handles cannot clear content from
// a newer invocation on the same element. Detach immediately, then let React
// finish the outer commit before unmounting a potentially nested root.
export function createManufacturingMarkdownRenderer() {
  let active = true;
  const owners = new Map<HTMLElement, () => void>();
  const renderMarkdown: ManufacturingMarkdownRenderer = (element, options) => {
    if (!active) return { unmount() {} };
    owners.get(element)?.();
    const child = document.createElement('div');
    element.append(child);
    const root = createRoot(child);
    let mounted = true;
    const unmount = () => {
      if (!mounted) return;
      mounted = false;
      if (owners.get(element) === unmount) owners.delete(element);
      child.remove();
      queueMicrotask(() => root.unmount());
    };
    owners.set(element, unmount);
    root.render(<ArticleMarkdown {...options} />);
    return { unmount };
  };
  return {
    renderMarkdown,
    unmount() {
      if (!active) return;
      active = false;
      for (const unmount of owners.values()) unmount();
    },
  };
}
