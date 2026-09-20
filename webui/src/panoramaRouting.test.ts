import { describe, expect, it } from 'vitest';
import { routePanoramaEdge, type PanoramaRect } from './panoramaRouting';
const bounds={width:800,height:600};
function points(path:string){return [...path.matchAll(/[ML] ([\d.-]+) ([\d.-]+)/g)].map(m=>({x:Number(m[1]),y:Number(m[2])}));}
function clear(path:string,obstacles:PanoramaRect[]){
 const p=points(path);expect(p.length).toBeGreaterThan(1);
 for(let i=1;i<p.length;i++){
  const a=p[i-1],b=p[i];expect(a.x===b.x || a.y===b.y).toBe(true);
  for(const r of obstacles){
   const crosses=a.x===b.x ? a.x>r.left&&a.x<r.right&&Math.max(a.y,b.y)>r.top&&Math.min(a.y,b.y)<r.bottom : a.y>r.top&&a.y<r.bottom&&Math.max(a.x,b.x)>r.left&&Math.min(a.x,b.x)<r.right;
   expect(crosses).toBe(false);
  }
 }
}
describe('panorama relationship routing',()=>{
 it('routes around an intervening node instead of drawing through its text',()=>{
  const from={left:20,top:240,right:180,bottom:310};const to={left:600,top:80,right:760,bottom:150};
  const obstacle={left:300,top:100,right:470,bottom:360};
  const path=routePanoramaEdge(from,to,[from,to,obstacle],bounds);clear(path,[from,to,obstacle]);
 });
 it('finds a route through staggered rows with more than one obstruction',()=>{
  const from={left:20,top:20,right:160,bottom:80};const to={left:600,top:480,right:760,bottom:560};
  const obstacles=[from,to,{left:190,top:0,right:340,bottom:420},{left:390,top:170,right:540,bottom:590}];
  clear(routePanoramaEdge(from,to,obstacles,bounds),obstacles);
 });
 it('keeps endpoints on node borders and routes unchanged geometry deterministically',()=>{
  const from={left:50,top:100,right:200,bottom:180};const to={left:50,top:320,right:200,bottom:400};
  const p=routePanoramaEdge(from,to,[from,to],bounds);clear(p,[from,to]);
  const coordinates=points(p);expect(coordinates[0]).toEqual({x:125,y:180});expect(coordinates[coordinates.length-1]).toEqual({x:125,y:320});
  expect(routePanoramaEdge(from,to,[from,to],bounds)).toBe(p);
 });
 it('does not invent a route for unmeasured or fully blocked geometry',()=>{
  const empty={left:0,top:0,right:0,bottom:0};expect(routePanoramaEdge(empty,empty,[empty],{width:0,height:0})).toBe('');
  const from={left:50,top:100,right:200,bottom:180};const to={left:600,top:320,right:750,bottom:400};
  expect(routePanoramaEdge(from,to,[from,to,{left:300,top:0,right:450,bottom:600}],bounds)).toBe('');
 });
});
