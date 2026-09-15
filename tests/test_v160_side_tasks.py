from shapely.geometry import LineString
from posting_navigator.routing import generate_route

def r(a,b,i): return {"id":i,"highway":"residential","geometry":LineString([a,b])}

def test_side_tasks_defer_opposite_side_on_grid():
    xs=[139.0,139.001,139.002]; ys=[35.0,35.001,35.002]
    roads=[]; i=1
    for y in ys:
        for a,b in zip(xs,xs[1:]): roads.append(r((a,y),(b,y),i)); i+=1
    for x in xs:
        for a,b in zip(ys,ys[1:]): roads.append(r((x,a),(x,b),i)); i+=1
    route=generate_route(roads,start_point=(xs[0],ys[0]))
    assert route["routing_strategy"]=="side-service-task-block-completion"
    assert route["routing_strategy"]=="side-service-task-block-completion"
    # Both side-service tasks must be completed at the theoretical ~2x floor.
    assert route["excess_over_two_side_m"] < 5
    assert 1.95 <= route["duplication_ratio"] <= 2.10

def test_dead_end_may_reverse_to_serve_other_side():
    roads=[r((139.0,35.0),(139.001,35.0),1)]
    route=generate_route(roads,start_point=(139.0,35.0))
    assert len([s for s in route["route_steps"] if not s.get("transfer")])==2
