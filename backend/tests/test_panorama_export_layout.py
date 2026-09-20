from copy import deepcopy
import xml.etree.ElementTree as ET
from app.ai_engineering.exports import render_svg
from app.ai_engineering.seed import PANORAMA_SEED

NS={'s':'http://www.w3.org/2000/svg'}

def test_export_retains_company_identity_and_two_way_default_process():
    root=ET.fromstring(render_svg(PANORAMA_SEED))
    text=' '.join(root.itertext())
    assert '3D 视觉感知' in text
    assert root.findall('.//s:polygon',NS)  # Visible arrowheads, also rasterized by PNG.
    assert any(r.attrib.get('fill')=='#152F50' for r in root.findall('.//s:rect',NS))

def test_export_follows_published_layer_group_order_and_edited_labels():
    data=deepcopy(PANORAMA_SEED)
    data['layers'].reverse()
    data['nodes'][0]['title']='新的供给节点'
    root=ET.fromstring(render_svg(data))
    labels=[t.text for t in root.findall('.//s:text',NS)]
    assert labels.index('支撑体系') < labels.index('产业位置')
    assert '新的供给节点' in labels


def test_reordered_industry_arrows_follow_supply_roles():
    from app.ai_engineering.exports import export_layout
    data=deepcopy(PANORAMA_SEED)
    data['layers'][0]['groups'].reverse()
    lines=[item for item in export_layout(data) if item['kind']=='line'][:2]
    assert all(item['points'][0][0] > item['points'][-1][0] for item in lines)
