import type { Route } from '../router';
export type PlatformPageLayout = 'standard' | 'reading' | 'canvas' | 'workspace';
export function platformPageLayout(route: Route, panorama: boolean): PlatformPageLayout | undefined {
  if (route.name === 'hr' || route.name.startsWith('hr-') || route.name.startsWith('fae-manage-')) return undefined;
  if (panorama && ['home','organization','ai-engineering'].includes(route.name)) return 'canvas';
  if (['ai-notes','ai-note','admin-agent-designs'].includes(route.name)) return 'reading';
  if (['home','brain','conversation','marketing','marketing-conversation'].includes(route.name)) return 'workspace';
  return 'standard';
}
