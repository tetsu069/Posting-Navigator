from posting_navigator.kmz import load_area_from_kmz

def test_extract_kita_shinjuku_1():
    poly = load_area_from_kmz("data/input/shinjuku_posting_map.kmz", "北新宿一丁目")
    assert poly.is_valid
    assert not poly.is_empty
    assert 139.69 < poly.centroid.x < 139.70
