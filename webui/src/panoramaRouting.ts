/** Route selected relationships through the gaps between measured node rectangles. */
export interface PanoramaRect { left:number; top:number; right:number; bottom:number }
interface Point { x:number; y:number }
interface Port { border:Point; outside:Point }
const GAP=5;
function inside(p:Point,r:PanoramaRect){return p.x>r.left && p.x<r.right && p.y>r.top && p.y<r.bottom;}
function crosses(a:Point,b:Point,r:PanoramaRect){
 return a.x===b.x
  ? a.x>r.left && a.x<r.right && Math.max(a.y,b.y)>r.top && Math.min(a.y,b.y)<r.bottom
  : a.y>r.top && a.y<r.bottom && Math.max(a.x,b.x)>r.left && Math.min(a.x,b.x)<r.right;
}
function ports(r:PanoramaRect):Port[]{
 const x=(r.left+r.right)/2,y=(r.top+r.bottom)/2;
 return [
  {border:{x,y:r.top},outside:{x,y:r.top-GAP}},
  {border:{x,y:r.bottom},outside:{x,y:r.bottom+GAP}},
  {border:{x:r.left,y},outside:{x:r.left-GAP,y}},
  {border:{x:r.right,y},outside:{x:r.right+GAP,y}},
 ];
}
function distance(a:Point,b:Point){return Math.abs(a.x-b.x)+Math.abs(a.y-b.y);}
function svgPath(points:Point[]):string{
 const reduced:Point[]=[];
 for(const p of points){
  const a=reduced[reduced.length-2],b=reduced[reduced.length-1];
  if(b && p.x===b.x && p.y===b.y)continue;
  if(a && b && ((a.x===b.x && b.x===p.x)||(a.y===b.y && b.y===p.y)))reduced.pop();
  reduced.push(p);
 }
 return reduced.map((p,i)=>`${i?'L':'M'} ${p.x} ${p.y}`).join(' ');
}
class Queue {
 private items:{id:number;rank:number}[]=[];
 push(id:number,rank:number){
  const entry={id,rank};this.items.push(entry);let i=this.items.length-1;
  while(i){const parent=(i-1)>>1;if(this.items[parent].rank<=rank)break;this.items[i]=this.items[parent];i=parent;}this.items[i]=entry;
 }
 pop():number|undefined{
  if(!this.items.length)return undefined;
  const result=this.items[0].id,last=this.items.pop()!;
  if(this.items.length){let i=0;
   while(i*2+1<this.items.length){let child=i*2+1;if(child+1<this.items.length&&this.items[child+1].rank<this.items[child].rank)child++;
    if(last.rank<=this.items[child].rank)break;this.items[i]=this.items[child];i=child;
   }this.items[i]=last;
  }return result;
 }
}
export function routePanoramaEdge(from:PanoramaRect,to:PanoramaRect,obstacles:PanoramaRect[],bounds:{width:number;height:number}):string{
 if(bounds.width<=0||bounds.height<=0||from.right<=from.left||from.bottom<=from.top||to.right<=to.left||to.bottom<=to.top)return '';
 const rectangles=obstacles.map(r=>({left:r.left-GAP,top:r.top-GAP,right:r.right+GAP,bottom:r.bottom+GAP}));
 const inBounds=(p:Point)=>p.x>=0&&p.y>=0&&p.x<=bounds.width&&p.y<=bounds.height;
 const usable=(p:Port)=>inBounds(p.outside)&&!rectangles.some(r=>inside(p.outside,r));
 const starts=ports(from).filter(usable),ends=ports(to).filter(usable);
 if(!starts.length||!ends.length)return '';
 const xs=[...new Set([0,bounds.width,...rectangles.flatMap(r=>[r.left,r.right]),...starts.map(p=>p.outside.x),...ends.map(p=>p.outside.x)])].filter(x=>x>=0&&x<=bounds.width).sort((a,b)=>a-b);
 const ys=[...new Set([0,bounds.height,...rectangles.flatMap(r=>[r.top,r.bottom]),...starts.map(p=>p.outside.y),...ends.map(p=>p.outside.y)])].filter(y=>y>=0&&y<=bounds.height).sort((a,b)=>a-b);
 const nx=xs.length,id=(p:Point)=>ys.indexOf(p.y)*nx+xs.indexOf(p.x);
 const point=(i:number):Point=>({x:xs[i%nx],y:ys[Math.floor(i/nx)]});
 const destinations=new Map(ends.map(p=>[id(p.outside),p]));
 const origins=new Map(starts.map(p=>[id(p.outside),p]));
 const estimate=(p:Point)=>Math.min(...ends.map(e=>distance(p,e.outside)));
 const queue=new Queue(),cost=new Map<number,number>(),previous=new Map<number,number>(),closed=new Set<number>();
 for(const [key,p] of origins){cost.set(key,0);queue.push(key,estimate(p.outside));}
 let current:number|undefined;
 while((current=queue.pop())!==undefined){
  if(closed.has(current))continue;closed.add(current);
  const destination=destinations.get(current);
  if(destination){
   const route:Point[]=[point(current)];let cursor=current;
   while(previous.has(cursor)){cursor=previous.get(cursor)!;route.push(point(cursor));}
   return svgPath([origins.get(cursor)!.border,...route.reverse(),destination.border]);
  }
  const x=current%nx,y=Math.floor(current/nx),a=point(current);
  const neighbors=[x>0?current-1:-1,x+1<nx?current+1:-1,y>0?current-nx:-1,y+1<ys.length?current+nx:-1];
  for(const next of neighbors){
   if(next<0||closed.has(next))continue;const b=point(next);
   if(rectangles.some(r=>inside(b,r)||crosses(a,b,r)))continue;
   const score=cost.get(current)!+distance(a,b);
   if(score<(cost.get(next)??Infinity)){cost.set(next,score);previous.set(next,current);queue.push(next,score+estimate(b));}
  }
 }
 return '';
}
