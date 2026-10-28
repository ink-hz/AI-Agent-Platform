import type { ManufacturingBootstrap } from './manufacturingApi';

/** Complete wire fixture; component tests exercise the same validator as the host. */
export function manufacturingBootstrap(overrides: Partial<ManufacturingBootstrap> = {}): ManufacturingBootstrap {
  return {allowed:true, available:true, can_edit:true, release:'test', protocol:1,
    workflow_available:false, workflow_access:false, legacy_access:true, configuration_admin:true,
    dataset:null, user:{id:'00000000-0000-4000-8000-000000000001',display_name:'Admin'}, project_ids:[], ...overrides};
}
