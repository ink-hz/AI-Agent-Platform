/** @vitest-environment jsdom */
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { expect, it, vi } from 'vitest';
import { HrPositionActions } from './HrPositionActions';
it('offers first actions without prior results and binds drafts to the selected position', async () => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const div = document.createElement('div'); const root = createRoot(div);
  const onDraft = vi.fn(); const onCandidates = vi.fn();
  try {
    await act(async () => root.render(<HrPositionActions title="嵌入式工程师" readOnly={false} onDraft={onDraft} onCandidates={onCandidates} />));
    const buttons = [...div.querySelectorAll('button')];
    expect(buttons.map(b => b.textContent)).toEqual(['梳理 JD / JR', '制定搜寻策略', '候选人 / 面试']);
    expect(onDraft).not.toHaveBeenCalled();
    await act(async () => buttons[0].click());
    expect(onDraft).toHaveBeenCalledWith(expect.stringContaining('嵌入式工程师'));
    await act(async () => buttons[2].click());
    expect(onCandidates).toHaveBeenCalledOnce();
    expect(onDraft).toHaveBeenCalledTimes(1);
    await act(async () => root.render(<HrPositionActions title="嵌入式工程师" readOnly onDraft={onDraft} onCandidates={onCandidates} />));
    expect([...div.querySelectorAll('button')].every(b => b.disabled)).toBe(true);
  } finally { await act(async () => root.unmount()); }
});
