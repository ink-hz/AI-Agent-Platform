import type { PanoramaData } from './panoramaTypes';

/** Minimal domain-neutral contract fixture for host/navigation regressions. */
export function panoramaTestData(title = '测试全景'): PanoramaData {
  return {
    title, version: 'test', updated_at: '2026-09-20', sources: [], edges: [],
    layers: [
      {id:'industry', title:'产业位置', kind:'industry', groups:[
        {id:'upstream', title:'上游', role:'upstream', columns:1, node_ids:['supplier']},
        {id:'company', title:'公司', role:'company', columns:1, node_ids:['company']},
        {id:'downstream', title:'下游', role:'downstream', columns:1, node_ids:['customer']},
      ]},
      {id:'portfolio', title:'产品与技术', kind:'portfolio', groups:[
        {id:'products', title:'产品', role:'products', columns:1, node_ids:['product']},
        {id:'technology', title:'技术', role:'technology', columns:1, node_ids:['technology']},
      ]},
      {id:'workflow', title:'Marketing ↔ Technology', kind:'workflow', groups:[
        {id:'marketing', title:'Marketing', role:'marketing', columns:1, node_ids:['market']},
        {id:'delivery', title:'Technology', role:'delivery', columns:1, node_ids:['delivery']},
      ]},
      {id:'support', title:'支撑体系', kind:'support', groups:[
        {id:'support', title:'支撑', role:'support', columns:1, node_ids:['digital']},
      ]},
    ],
    nodes: ['supplier','company','customer','product','technology','market','delivery','digital'].map(id => ({
      id, title: id === 'digital' ? '数字化与知识' : id, subtitle:'', detail:[], source_ids:[],
      actions: id === 'digital' ? ['brain','notes','sessions'] : [],
    })),
  };
}
