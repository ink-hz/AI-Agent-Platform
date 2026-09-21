import type { Route } from './router';
import type { PanoramaActionId } from './panoramaTypes';

const PATHS: Record<PanoramaActionId, string> = {
 brain: '/brain', agents: '/agents', missions: '/missions', sessions: '/admin/sessions',
 operations: '/admin', review: '/admin/review', activity: '/admin/activity', identity: '/admin/identity',
 governance: '/admin/governance', access: '/admin/access', account: '/account', 'agent-admin': '/admin/agents',
 notes: '/ai-notes', hr: '/hr/', office: '/office/', voc: '/voc/', fae: '/fae/',
};
export function actionPath(action: PanoramaActionId) {
 return { path: PATHS[action], external: ['hr', 'office', 'voc', 'fae'].includes(action) };
}
export function workspaceGroup(route: Route): PanoramaActionId | null {
 switch(route.name) {
 case 'brain': case 'conversation': case 'conversations': return 'brain';
 case 'agents': return 'agents';
 case 'marketing': case 'marketing-conversation': return 'agents';
 case 'missions': case 'mission': return 'missions';
 case 'admin-sessions': case 'admin-session': return 'sessions';
 case 'admin-agents': case 'admin-agent': case 'admin-agent-runtime': return 'agent-admin';
 case 'admin-overview': return 'operations';
 case 'admin-review': return 'review';
 case 'admin-activity': return 'activity';
 case 'admin-identity': return 'identity';
 case 'admin-governance': return 'governance';
 case 'admin-access': return 'access';
 case 'account': return 'account';
 case 'ai-notes': case 'ai-note': return 'notes';
 default: return null;
 }
}
export function isPanoramaLocation(route: Route): boolean {
 return route.name === 'home' || route.name === 'organization' || route.name === 'ai-engineering'
   || (window.history.state?.panorama === true && workspaceGroup(route) !== null);
}

let leaveGuard: ((path: string) => boolean) | undefined;
export function registerPanoramaLeaveGuard(guard: (path: string) => boolean): () => void {
 leaveGuard = guard;
 return () => { if (leaveGuard === guard) leaveGuard = undefined; };
}
export function allowPanoramaNavigation(path: string): boolean { return leaveGuard?.(path) ?? true; }
