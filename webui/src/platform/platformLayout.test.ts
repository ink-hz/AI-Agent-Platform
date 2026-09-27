import { expect, it } from 'vitest';
import { platformPageLayout } from './platformLayout';
it('uses the company canvas only for company views, including while other platform destinations remain visible', () => {
 expect(platformPageLayout({name:'home'},true)).toBe('canvas');
 expect(platformPageLayout({name:'organization'},true)).toBe('canvas');
 expect(platformPageLayout({name:'admin-review'},true)).toBe('standard');
});
it('shares a reading frame and keeps conversations in their workspace', () => {
 expect(platformPageLayout({name:'admin-agent-designs'},false)).toBe('reading');
 expect(platformPageLayout({name:'ai-notes'},false)).toBe('reading');
 expect(platformPageLayout({name:'brain'},false)).toBe('workspace');
 expect(platformPageLayout({name:'conversation',conversationId:'c'},false)).toBe('workspace');
 expect(platformPageLayout({name:'admin-identity'},false)).toBe('standard');
});
it('excludes independent and legacy embedded business applications', () => {
 expect(platformPageLayout({name:'hr'},false)).toBeUndefined();
 expect(platformPageLayout({name:'hr-position',positionId:'p'},false)).toBeUndefined();
 expect(platformPageLayout({name:'fae-manage-sessions'},false)).toBeUndefined();
});
