from shapely.geometry import LineString

from posting_navigator.routing import _RoadSourceIndex, _dedupe_roads_by_geometry, build_graph


def _road(coords, name, posting=True):
    return {
        "geometry": LineString(coords),
        "name": name,
        "highway": "residential",
        "posting_target": posting,
        "residential_score": 1.0,
        "nonresidential_overlap": 0.0,
    }


def test_source_index_returns_nearest_road_without_global_scan():
    roads = [
        _road([(0, 0), (0.001, 0)], "near"),
        _road([(1, 1), (1.001, 1)], "far"),
    ]
    idx = _RoadSourceIndex(roads)
    src = idx.source_for_segment(LineString([(0.0004, 0), (0.0006, 0)]))
    assert src["name"] == "near"


def test_connector_duplicates_are_removed():
    a = _road([(0, 0), (0.001, 0)], "a", posting=False)
    b = dict(a)
    b["posting_target"] = True
    unique = _dedupe_roads_by_geometry([a, b])
    assert len(unique) == 1
    assert unique[0]["posting_target"] is True


def test_dense_parallel_roads_build_with_spatial_index():
    roads = []
    for i in range(160):
        y = i * 0.00001
        roads.append(_road([(0, y), (0.002, y)], f"r{i}"))
    g = build_graph(roads, simplify=False)
    assert g.number_of_edges() >= 160
