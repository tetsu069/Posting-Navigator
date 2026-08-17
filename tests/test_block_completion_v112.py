import networkx as nx
from shapely.geometry import LineString
from posting_navigator.routing import generate_route


def r(a,b):
    return {"geometry": LineString([a,b]), "highway":"residential", "name":"", "id":None}


def test_comb_teeth_are_completed_when_trunk_is_visited():
    # 横幹＋下向きの3本の袋小路。各枝はその周辺通過時に往復してから先へ進むべき。
    roads=[]
    xs=[139.7000,139.7005,139.7010,139.7015]
    y=35.7000
    for a,b in zip(xs,xs[1:]): roads.append(r((a,y),(b,y)))
    for x in xs[1:]: roads.append(r((x,y),(x,y-0.00035)))
    route=generate_route(roads,start_point=(xs[0],y))
    seq=[(s['from'],s['to']) for s in route['route_steps']]
    # 各歯の先端へ行ったら、次辺は必ず同じ幹交差点へ戻る（後回しにしない）。
    teeth={(round(x,6),round(y-0.00035,6)) for x in xs[1:]}
    for i,(a,b) in enumerate(seq[:-1]):
        if (round(b[0],6),round(b[1],6)) in teeth:
            assert seq[i+1][1] == a


def test_grid_does_not_create_excessive_long_revisits():
    # 4x4格子。ブロックの一部を残して大きく戻る動きが過剰でないことを確認。
    roads=[]
    xs=[139.70+i*0.00035 for i in range(4)]
    ys=[35.70+j*0.00030 for j in range(4)]
    for y in ys:
        for a,b in zip(xs,xs[1:]): roads.append(r((a,y),(b,y)))
    for x in xs:
        for a,b in zip(ys,ys[1:]): roads.append(r((x,a),(x,b)))
    route=generate_route(roads,start_point=(xs[0],ys[-1]))
    steps=route['route_steps']
    assert route['duplication_ratio'] < 1.55
    # ルートは全対象道路をカバーする。
    assert route['route_edges'] >= route['source_edges']
    assert route['routing_strategy']=='block-completion-comb-grid-sweep'
