from shapely.geometry import LineString, Polygon

from posting_navigator.osm import _ContextIndex, _road_context_score


def test_spatial_index_scores_nearby_housing_without_global_union():
    # 道路近傍の住宅だけが検索対象になることを確認する。
    buildings = [
        Polygon([(0, 4), (8, 4), (8, 12), (0, 12)]),
        Polygon([(1000, 1000), (1010, 1000), (1010, 1010), (1000, 1010)]),
    ]
    context = _ContextIndex(buildings, [])
    road = LineString([(0, 0), (100, 0)])
    target, score, overlap, nearest = _road_context_score(road, "residential", "", context)
    assert target is True
    assert nearest == 4.0
    assert score > 0.5
    assert overlap == 0.0
    assert len(context.nearby_buildings(road, 50)) == 1


def test_nonresidential_path_becomes_connector_only():
    park = Polygon([(-10, -10), (110, -10), (110, 20), (-10, 20)])
    context = _ContextIndex([], [park])
    road = LineString([(0, 0), (100, 0)])
    target, score, overlap, nearest = _road_context_score(road, "footway", "", context)
    assert target is False
    assert overlap > 0.9
    assert nearest > 45
    assert score < 0.2
