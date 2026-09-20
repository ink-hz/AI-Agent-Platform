"""Render the published overview from the same ordered groups used by the homepage."""
from __future__ import annotations

import html
import io
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1920, 1080
INK, MUTED, BORDER = '#152B42', '#607389', '#D8E2EC'

# Business-role colors match panorama.css; role identity survives selection/reordering.
ROLE_COLORS = {'upstream': ('#E8EEF5', '#B6C5D6', '#43566E'),
 'company': ('#152F50', '#152F50', '#FFFFFF'),
 'downstream': ('#D5F2ED', '#77C9BA', '#11665B'),
 'products': ('#DCEAFF', '#8AB4F5', '#184C9D'),
 'technology': ('#E9E0FA', '#B4A0DF', '#644096'),
 'marketing': ('#FFEDCD', '#E9BE6C', '#89510B'),
 'delivery': ('#DCEAFF', '#8AB4F5', '#184C9D'),
 'support': ('#E2EAF2', '#A9BDCF', '#3B5670')}


@lru_cache(maxsize=16)
def _font(size: int):
    for path in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/System/Library/Fonts/STHeiti Light.ttc'):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise RuntimeError('PNG export requires a CJK font (install fonts-noto-cjk)')


def _shorten(text: str, width: float, size: int) -> str:
    # Fixed typography: collapse labels, never shrink the whole diagram to unreadable text.
    font = _font(size)
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(text + '…') > width:
        text = text[:-1]
    return text + '…'


def export_layout(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    nodes = {n['id']: n for n in data['nodes']}

    def rect(x, y, w, h, fill='white', stroke=BORDER):
        items.append(dict(kind='rect', x=x, y=y, w=w, h=h, fill=fill, stroke=stroke))

    def text(x, y, value, size=22, color=INK, anchor='start', width=1800):
        items.append(dict(kind='text', x=x, y=y, text=_shorten(value, width, size), full=value, size=size, fill=color, anchor=anchor))

    def arrow(points, both=False, head=True):
        items.append(dict(kind='line', points=points, fill='#8294A8'))
        ends = [(points[-2], points[-1])] if head else []
        if both:
            ends.append((points[1], points[0]))
        for before, tip in ends:
            angle = math.atan2(tip[1]-before[1], tip[0]-before[0])
            a=(tip[0]-8*math.cos(angle)+3.5*math.sin(angle), tip[1]-8*math.sin(angle)-3.5*math.cos(angle))
            b=(tip[0]-8*math.cos(angle)-3.5*math.sin(angle), tip[1]-8*math.sin(angle)+3.5*math.cos(angle))
            items.append(dict(kind='arrow', points=[tip,a,b], fill='#8294A8'))

    def grid(group, x, y, w, h):
        ids=group['node_ids']; count=len(ids)
        if not count:
            return []
        cols=min(group['columns'], count); rows=math.ceil(count/cols)
        # Expanded groups belong in the interactive detail; the overview keeps its scale.
        max_rows=max(1, int((h+10)//40)); visible=ids[:cols*max_rows]
        rows=min(rows,max_rows); cw=(w-12*(cols-1))/cols; ch=(h-10*(rows-1))/rows
        boxes=[]
        for i, node_id in enumerate(visible):
            n=nodes[node_id]; left=x+(i%cols)*(cw+12); top=y+(i//cols)*(ch+10)
            company=group['role']=='company'
            fill,stroke,ink=ROLE_COLORS[group['role']]
            rect(left,top,cw,ch,fill,stroke)
            label=n['title'] if i<len(visible)-1 or len(ids)==len(visible) else '更多内容'
            if company and n['subtitle'] and ch>=80:
                text(left+cw/2,top+ch/2-2,label,32,'white','middle',cw-28)
                text(left+cw/2,top+ch/2+31,n['subtitle'],21,'#CCDBEB','middle',cw-28)
            else:
                text(left+cw/2,top+ch/2+8,label,22,ink,'middle',cw-24)
            boxes.append((left,top,cw,ch))
        return boxes

    rect(0,0,WIDTH,HEIGHT,'#F7F9FC','#F7F9FC')
    text(54,52,data['title'],30)
    text(1864,48,'内容版本 '+data['version'],14,MUTED,'end',630)
    sizes={'industry':222,'portfolio':266,'workflow':256,'support':170}
    y=88
    for layer in data['layers']:
        kind=layer['kind']; height=sizes[kind]
        text(54,y+22,layer['title'],21,MUTED)
        if kind=='industry':
            widths={'upstream':400,'company':480,'downstream':800}
            x=72; group_boxes={}
            for group in layer['groups']:
                w=widths[group['role']]
                if group['role']!='company':
                    text(x,y+51,group['title'],18,MUTED,width=w)
                boxes=grid(group,x,y+68,w,132)
                group_boxes[group['role']]=(x,y+68,w,132)
                x+=w+60
            for source_role,target_role in [('upstream','company'),('company','downstream')]:
                source,target=group_boxes[source_role],group_boxes[target_role]
                forward=source[0]<target[0]
                start=(source[0]+source[2]+9 if forward else source[0]-9,source[1]+source[3]/2)
                end=(target[0]-12 if forward else target[0]+target[2]+12,target[1]+target[3]/2)
                if abs(end[0]-start[0])<60:
                    arrow([start,end])
                else:
                    # Reordered roles can be separated by another group: use the free top lane.
                    lane=y+59
                    arrow([start,(start[0],lane),(end[0],lane),end])
        elif kind=='portfolio':
            rect(48,y+38,1824,height-42)
            regions={}
            for i,group in enumerate(layer['groups']):
                gy=y+62+i*117
                if group['role']=='technology':
                    rect(62,gy-7,1796,84,'#F0E9FA','#F0E9FA')
                grid(group,74,gy,1772,70)
                regions[group['role']]=(74,gy,1772,70)
            product,tech=regions['products'],regions['technology']
            upward=tech[1]>product[1]
            py=product[1]+product[3]+12 if upward else product[1]-12
            ty=tech[1]-7 if upward else tech[1]+tech[3]+7
            arrow([(94,py-6 if upward else py+6),(94,py),(1828,py),(1828,py-6 if upward else py+6)],head=False)
            arrow([(960,ty),(960,py)])
            text(934,(ty+py)/2+6,'技术支撑',17,MUTED,'end',160)
        elif kind=='workflow':
            rect(48,y+38,1824,height-42)
            rows=[]
            for i,group in enumerate(layer['groups']):
                gy=y+62+i*88
                text(76,gy+38,group['title'],22,ROLE_COLORS[group['role']][2],width=190)
                boxes=grid(group,288,gy,1548,62)
                rows.append(boxes)
                for a,b in zip(boxes,boxes[1:]):
                    if a[1]==b[1]:
                        arrow([(a[0]+a[2]+2,a[1]+a[3]/2),(b[0]-2,b[1]+b[3]/2)],both=True)
            if len(rows)==2:
                for a,b in zip(rows[0],rows[1]):
                    # Handoffs link corresponding stages only when columns line up.
                    if abs(a[0]+a[2]/2-b[0]-b[2]/2)<1:
                        arrow([(a[0]+a[2]/2,a[1]+a[3]+4),(b[0]+b[2]/2,b[1]-4)],both=True)
                if rows[1]:
                    first,last=rows[1][0],rows[1][-1]; fy=y+height-12
                    arrow([(last[0]+last[2]/2,last[1]+last[3]+3),(last[0]+last[2]/2,fy),(first[0]+first[2]/2,fy),(first[0]+first[2]/2,first[1]+first[3]+3)])
        else:
            # Support groups stay ordered and occupy their own rows if the data evolves.
            gh=(height-55)/len(layer['groups'])
            for i,group in enumerate(layer['groups']):
                grid(group,48,y+49+i*gh,1824,gh-12)
        y+=height+14
    return items


def render_svg(data: dict[str, Any]) -> bytes:
    blocks=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">', '<style>text{font-family:"Noto Sans CJK SC","PingFang SC",sans-serif}</style>']
    for item in export_layout(data):
        kind=item['kind']
        if kind=='rect':
            blocks.append(f'<rect x="{item["x"]}" y="{item["y"]}" width="{item["w"]}" height="{item["h"]}" rx="12" fill="{item["fill"]}" stroke="{item["stroke"]}"/>')
        elif kind=='text':
            blocks.append(f'<text x="{item["x"]}" y="{item["y"]}" font-size="{item["size"]}" fill="{item["fill"]}" text-anchor="{item["anchor"]}">{html.escape(item["text"])}<title>{html.escape(item["full"])}</title></text>')
        else:
            points=' '.join(f'{x},{y}' for x,y in item['points'])
            if kind=='line':
                blocks.append(f'<polyline points="{points}" fill="none" stroke="{item["fill"]}" stroke-width="2"/>')
            else:
                blocks.append(f'<polygon points="{points}" fill="{item["fill"]}"/>')
    for node in data['nodes']:
        blocks.append(f'<desc>{html.escape(node["title"])}</desc>')
    for edge in data['edges']:
        blocks.append(f'<desc>{html.escape(edge["from"])} → {html.escape(edge["to"])}: {html.escape(edge["label"])}</desc>')
    blocks.append('</svg>')
    return ''.join(blocks).encode()


def render_png(data: dict[str, Any]) -> bytes:
    image=Image.new('RGB',(WIDTH,HEIGHT),'#F7F9FC'); draw=ImageDraw.Draw(image)
    for item in export_layout(data):
        kind=item['kind']
        if kind=='rect':
            draw.rounded_rectangle((item['x'],item['y'],item['x']+item['w'],item['y']+item['h']),12,fill=item['fill'],outline=item['stroke'])
        elif kind=='text':
            anchor={'start':'ls','middle':'ms','end':'rs'}[item['anchor']]
            draw.text((item['x'],item['y']),item['text'],fill=item['fill'],font=_font(item['size']),anchor=anchor)
        elif kind=='line':
            draw.line(item['points'],fill=item['fill'],width=2)
        else:
            draw.polygon(item['points'],fill=item['fill'])
    output=io.BytesIO(); image.save(output,'PNG',optimize=True); return output.getvalue()
