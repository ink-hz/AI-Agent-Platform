/** @vitest-environment jsdom */
import { readFileSync } from 'node:fs';
import { expect, it } from 'vitest';
import { AI_NOTES_MERMAID_CONFIG } from './components/ai-notes/MermaidDiagram';

const snapshot=process.env.TEST_MANUFACTURING_HANDBOOK;
it.skipIf(!snapshot)('parses every Mermaid fence in the actual release handbook with the shared strict configuration',async()=>{
 const markdown=readFileSync(snapshot!,'utf8');
 const blocks=[...markdown.matchAll(/^```mermaid\s*\r?\n([\s\S]*?)^```\s*$/gm)];
 expect(blocks.length).toBeGreaterThan(0);
 const {default:mermaid}=await import('mermaid');
 mermaid.initialize(AI_NOTES_MERMAID_CONFIG);
 for(const block of blocks) expect(await mermaid.parse(block[1])).toBeTruthy();
},30000);
