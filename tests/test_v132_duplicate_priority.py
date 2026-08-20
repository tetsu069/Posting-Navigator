from shapely.geometry import LineString
from posting_navigator.routing import _local_completion_quality


def edge(a,b,length,duplicated=False,highway='residential'):
    return (a,b,0,{'length': length, 'duplicated': duplicated, 'highway': highway, 'geometry': LineString([a,b])})


def test_quality_prefers_less_duplicate_distance_before_late_revisit_cosmetics():
    # Both candidates have no immediate reverse. Candidate A revisits a node later,
    # but candidate B wastes 300m on duplicated road. Field route must choose A.
    a=(0,0); b=(0.001,0); c=(0.002,0); d=(0.003,0); e=(0.004,0)
    short=[edge(a,b,50), edge(b,c,50), edge(c,d,20,True), edge(d,e,50)]
    waste=[edge(a,b,50), edge(b,c,50), edge(c,d,300,True), edge(d,e,50)]
    assert _local_completion_quality(short) < _local_completion_quality(waste)


def test_quality_penalizes_major_road_duplicate_before_residential_duplicate():
    a=(0,0); b=(0.001,0); c=(0.002,0)
    residential=[edge(a,b,80,True,'residential'), edge(b,c,20)]
    major=[edge(a,b,80,True,'primary'), edge(b,c,20)]
    assert _local_completion_quality(residential) < _local_completion_quality(major)
