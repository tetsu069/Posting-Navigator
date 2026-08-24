from shapely.geometry import LineString
from posting_navigator.osm import _suppress_divided_major_centerlines


def road(y, highway='residential', required=True, boundary=False):
    return {
        'highway': highway, 'required': required, 'boundary_near': boundary,
        '_geom_m': LineString([(0, y), (120, y)])
    }


def test_major_centerline_optional_when_flanked_both_sides():
    center = road(0, 'primary')
    roads = [center, road(18), road(-18)]
    _suppress_divided_major_centerlines(roads)
    assert center['required'] is False
    assert center['center_carriageway_optional'] is True


def test_major_kept_when_only_one_side_has_walkable_road():
    center = road(0, 'primary')
    roads = [center, road(18)]
    _suppress_divided_major_centerlines(roads)
    assert center['required'] is True


def test_boundary_major_is_never_suppressed():
    center = road(0, 'primary', boundary=True)
    roads = [center, road(18), road(-18)]
    _suppress_divided_major_centerlines(roads)
    assert center['required'] is True
