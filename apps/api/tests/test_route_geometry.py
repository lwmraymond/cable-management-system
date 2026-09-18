from types import SimpleNamespace
import pytest
from app.exceptions import ValidationError
from app.services.route_geometry import RouteGeometry, choose_portions


def geometry(points, recorded=None):
    return RouteGeometry.from_segment(
        SimpleNamespace(
            coordinates=[dict(zip(("x", "y", "z"), p)) for p in points], length_m=recorded
        )
    )


def test_projection_preserves_bends_vertical_segments_and_reverse_order():
    g = geometry([(0, 0, 0), (0, 0, 0), (4, 0, 0), (4, 3, 0), (4, 3, 2)], 18)
    assert g.length == 9
    assert g.project((2, 1, 0)) == (2, 1)
    assert g.project((5, 3, 1)) == (8, 1)
    parts, access = choose_portions([g], (5, 3, 1), (2, 1, 0))
    assert parts == [(8, 2)] and access == 2
    assert g.adopted_length(*parts[0]) == 12
    assert g.at(8) == (4, 3, 1)


def test_common_projection_is_a_zero_length_tray_portion_with_real_access_lengths():
    g = geometry([(0, 0, 3), (4, 0, 3)])
    assert choose_portions([g], (2, 0, 0), (2, 0, 1)) == ([(2, 2)], 5)


def test_looped_polyline_orientation_search_is_bounded_and_handles_reversed_middle():
    first = geometry([(0, 0, 0), (2, 0, 0)])
    middle = geometry([(4, 0, 0), (2, 0, 0)])
    last = geometry([(4, 0, 0), (6, 0, 0)])
    parts, access = choose_portions([first, middle, last], (1, 0, 1), (5, 0, 1))
    assert parts == [(1, 2), (2, 0), (0, 1)] and access == 2
    with pytest.raises(ValidationError, match="disconnected"):
        choose_portions([first, last], (1, 0, 1), (5, 0, 1))


@pytest.mark.parametrize(
    "points",
    [[(0, 0, 0), (0, 0, 0)], [(0, 0, 0), (float("inf"), 0, 0)], [(False, 0, 0), (1, 0, 0)]],
)
def test_invalid_geometries_are_not_routable(points):
    with pytest.raises(ValidationError):
        geometry(points)
