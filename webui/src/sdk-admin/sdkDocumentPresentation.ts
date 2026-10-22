import type { DocumentKind, DocumentSummary } from './sdkAdminApi';

export const kindNames: Record<DocumentKind, string> = {
    knowledge: '代码知识', process: '流程与规范', analysis: '分析成果',
    design: '设计方案', acceptance: '接收记录', execution: '执行记录'
};
const statusNames = new Map<string, string>(Object.entries({
    accepted_static: '静态接收', navigation: '阅读导航', phase_report: '阶段报告',
    design_proposal: '设计草案', execution_record: '执行记录', independently_reviewed: '已独立复核',
    acceptance_report: '接收报告', not_applicable: '不适用', source_record: '来源记录',
    not_run: '尚未进行设备实测', instance_not_verified: '实例待核验',
    limited_release_record: '有限发布记录', not_registered: '待登记'
}));
export function statusLabel(code: string) { return statusNames.get(code) ?? '状态待确认'; }

// Describe only declared evidence. Static acceptance never implies device testing.
export function documentDescription(doc: DocumentSummary): string {
    let description: string;
    switch (doc.content_state) {
        case 'design_proposal': description = '设计草案，不代表已实施'; break;
        case 'phase_report': description = '阶段性分析，结论限于文中范围'; break;
        case 'accepted_static': description = doc.kind === 'knowledge' ? '源码静态解读' : '静态资料接收记录'; break;
        case 'navigation': description = '阅读导航'; break;
        case 'execution_record': description = '执行记录，实际结果以正文证据为准'; break;
        default: description = '内容状态待确认';
    }
    if (doc.runtime_validation === 'not_run') description += '，尚未进行设备实测';
    else if (doc.runtime_validation !== 'not_applicable') description += '，运行验证状态待确认';
    return `${description}。`;
}
const conditionNames = new Map(Object.entries({
    device: '设备', devices: '设备', firmware: '固件', macros: '编译宏',
    platforms: '平台', platform: '平台', plugins: '插件', states: '运行状态',
    configuration: '配置', config: '配置', state: '运行状态'
}));
const conditionValues = new Map(Object.entries({
    all_applicable: '适用于文中所述范围',
    all_applicable_to_described_component_or_data_layer: '适用于文中所述组件或数据层',
    not_checked_by_this_layer: '本层未核验固件条件',
    explicit_in_each_api_card: '以各 API 卡片声明为准',
    none_in_cited_branch: '引用的源码分支未要求编译宏',
    actual_compiled_PAL_or_platform_independent_for_pure_data: '以实际编译的平台适配层为准；纯数据层与平台无关',
    platform_independent_for_this_layer: '本层实现与平台无关',
    not_required_by_this_entry: '此入口不要求插件',
    only_when_described_creator_or_component_requires: '仅在文中所述创建入口或组件要求时需要插件',
    valid_SDK_handles_and_described_stream_states: '有效的 SDK 句柄及文中规定的流状态'
}));
export function conditionLabel(key: string) { return conditionNames.get(key) ?? key; }
export function conditionDescription(value: unknown): string {
    if (Array.isArray(value)) return value.map(conditionDescription).join('；');
    if (typeof value === 'string') return conditionValues.get(value) ?? value;
    return JSON.stringify(value);
}
