import { BookOpen, Boxes, CircleHelp, Compass, GitBranch } from 'lucide-react';
import type { SdkAdminSection } from './sdkAdminPaths';

// The platform sidebar and SDK page heading describe the same workspace sections.
export const SDK_ADMIN_SECTIONS = [
  { id: 'overview', path: '/sdk-admin', title: '总览', subtitle: '目标与下一动作', icon: Compass },
  { id: 'products', path: '/sdk-admin/products', title: '产品与版本', subtitle: '范围、来源与业务对象', icon: Boxes },
  { id: 'delivery', path: '/sdk-admin/delivery', title: '研发与交付', subtitle: '阶段标准与交接依据', icon: GitBranch },
  { id: 'diagnostics', path: '/sdk-admin/diagnostics', title: '诊断与改进', subtitle: '技术与过程中的问题', icon: CircleHelp },
  { id: 'knowledge', path: '/sdk-admin/knowledge', title: '知识与成果', subtitle: '深入阅读与证据检索', icon: BookOpen },
] as const satisfies readonly { id: SdkAdminSection; path: string; title: string; subtitle: string; icon: typeof BookOpen }[];

export function sdkAdminSection(section: SdkAdminSection) {
  return SDK_ADMIN_SECTIONS.find(item => item.id === section)!;
}
