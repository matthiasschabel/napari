from __future__ import annotations

import pickle
import subprocess
import sys
from copy import deepcopy
from dataclasses import FrozenInstanceError

import numpy as np
import pytest
from shapely.geometry import (
    MultiPolygon as ShapelyMultiPolygon,
    Polygon as ShapelyPolygon,
)

from napari.experimental._data_model import (
    ROI,
    ArraySource,
    CoordinateAxis,
    CoordinateFrame,
    CoordinateSelection,
    DataObject,
    Field,
    Interpolation,
    PlaneAnchor,
    Polygon,
    PolygonSetDomain,
    ROICollection,
    StructuredGridDomain,
)


def square(minimum: float = 0.0, maximum: float = 10.0) -> np.ndarray:
    return np.array(
        [
            [minimum, minimum],
            [maximum, minimum],
            [maximum, maximum],
            [minimum, maximum],
        ],
        dtype=float,
    )


def test_geometry_import_error_names_shapely_install() -> None:
    code = """
import builtins

original_import = builtins.__import__

def without_shapely(name, *args, **kwargs):
    if name == 'shapely' or name.startswith('shapely.'):
        raise ImportError('blocked for dependency-message test')
    return original_import(name, *args, **kwargs)

builtins.__import__ = without_shapely
try:
    import napari.experimental._data_model._geometry
except ImportError as exc:
    assert "pip install shapely" in str(exc), str(exc)
else:
    raise AssertionError('geometry import unexpectedly succeeded')
"""

    result = subprocess.run(
        [sys.executable, '-c', code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_mpr_direct_import_error_names_shapely_install() -> None:
    code = """
import importlib
import sys

sys.modules['shapely'] = None
try:
    importlib.import_module('napari.experimental._data_model._mpr')
except ImportError as exc:
    assert "pip install shapely" in str(exc), str(exc)
else:
    raise AssertionError('MPR import unexpectedly succeeded')
"""

    result = subprocess.run(
        [sys.executable, '-c', code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def make_target(name: str = 'image') -> DataObject:
    domain = StructuredGridDomain(
        (
            CoordinateAxis('EchoTime', 2, unit='ms'),
            CoordinateAxis('S', 3, unit='mm'),
            CoordinateAxis('P', 4, unit='mm'),
            CoordinateAxis('L', 5, unit='mm'),
        )
    )
    return DataObject(name, domain)


def make_roi(
    target: DataObject,
    *,
    name: str = 'lesion',
    plane_value: float = 12.0,
    context: dict[str, object] | None = None,
) -> ROI:
    domain = PolygonSetDomain((Polygon(square()),), ('L', 'P'), ('mm', 'mm'))
    data = DataObject(
        f'{name} data',
        domain,
        {
            'label': Field(
                'label',
                ArraySource(np.array([name])),
                interpolation=Interpolation.NEAREST,
            ),
            't2star_ms': Field(
                't2star_ms', ArraySource(np.array([18.5])), unit='ms'
            ),
        },
    )
    frame = CoordinateFrame('LPS', (('L', 'mm'), ('P', 'mm'), ('S', 'mm')))
    anchor = PlaneAnchor(
        frame,
        'S',
        plane_value,
        ('L', 'P'),
        {} if context is None else context,
    )
    return ROI(name, data, anchor, target)


def test_polygon_copies_and_freezes_rings() -> None:
    exterior = square()
    hole = square(3.0, 7.0)

    polygon = Polygon(exterior, (hole,))
    exterior[0] = 99.0
    hole[0] = 99.0

    np.testing.assert_array_equal(polygon.exterior[0], [0.0, 0.0])
    np.testing.assert_array_equal(polygon.holes[0][0], [3.0, 3.0])
    assert not polygon.exterior.flags.writeable
    assert not polygon.holes[0].flags.writeable
    assert polygon.exterior is polygon.exterior
    assert polygon.holes is polygon.holes
    assert repr(polygon) == 'Polygon(n_holes=1, n_vertices=8)'
    with pytest.raises(ValueError, match='read-only'):
        polygon.exterior[0, 0] = 1.0


@pytest.mark.parametrize(
    ('ring', 'message'),
    [
        (np.ones((2, 2)), 'at least three'),
        (np.ones(3), r'shape \(n, 2\)'),
        (np.ones((3, 3)), r'shape \(n, 2\)'),
        (np.array([[0, 0], [1, 0], [np.inf, 1]]), 'finite'),
        (
            np.array([[0, 0], [1, 0], [1, 1], [0, 0]]),
            'implicitly closed',
        ),
        (
            np.array([[0, 0], [2, 0], [2, 2], [0, 2], [2, 0]]),
            'unique',
        ),
    ],
)
def test_polygon_rejects_invalid_exterior(
    ring: np.ndarray, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Polygon(ring)


def test_polygon_rejects_invalid_interior_ring() -> None:
    with pytest.raises(ValueError, match='interior ring'):
        Polygon(square(), (np.ones((2, 2)),))


def test_polygon_rejects_string_holes_input() -> None:
    with pytest.raises(TypeError, match='iterable of rings'):
        Polygon(square(), 'not rings')


def test_polygon_rejects_hole_vertices_outside_exterior() -> None:
    # Shapely validity subsumes the former per-vertex containment check and
    # also catches edge crossings that a vertex-only check could miss.
    with pytest.raises(ValueError, match='invalid polygon geometry'):
        Polygon(square(), (square(8.0, 12.0),))


def test_polygon_rejects_hole_sharing_exterior_edge() -> None:
    edge_sharing_hole = np.array(
        [[2.0, 0.0], [8.0, 0.0], [8.0, 2.0], [2.0, 2.0]]
    )

    # The pre-Shapely boundary-inclusive vertex check accepted this hole.
    with pytest.raises(ValueError, match='invalid polygon geometry'):
        Polygon(square(), (edge_sharing_hole,))


def test_polygon_rejects_collinear_zero_area_exterior() -> None:
    collinear = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])

    # The pre-Shapely winding normalizer accepted rings with zero signed area.
    with pytest.raises(ValueError, match='invalid polygon geometry'):
        Polygon(collinear)


def test_polygon_rejects_self_intersection() -> None:
    bow_tie = np.array([[0.0, 0.0], [2.0, 2.0], [0.0, 2.0], [2.0, 0.0]])

    with pytest.raises(ValueError, match='invalid polygon geometry'):
        Polygon(bow_tie)


def test_polygon_containment_area_and_bounding_box() -> None:
    polygon = Polygon(square(), (square(3.0, 7.0),))
    points = np.array(
        [
            [1.0, 1.0],
            [5.0, 5.0],
            [9.0, 5.0],
            [11.0, 5.0],
            [0.0, 5.0],
            [3.0, 5.0],
        ]
    )

    np.testing.assert_array_equal(
        polygon.contains_points(points),
        # Shapely contains_xy uses strict interior semantics. Both exterior
        # and hole boundaries are excluded.
        [True, False, True, False, False, False],
    )
    np.testing.assert_allclose(
        polygon.bounding_box,
        [[0.0, 0.0], [10.0, 10.0]],
        rtol=0.0,
        atol=0.0,
    )
    assert polygon.area == pytest.approx(84.0, rel=0.0, abs=1e-12)


def test_polygon_contains_points_swept_just_inside_rotated_edge() -> None:
    triangle = np.array([[0.13, 0.17], [9.71, 3.43], [1.9, 8.2]])
    fractions = np.linspace(0.0, 1.0, 19)
    edge_points = triangle[0] + fractions[:, None] * (
        triangle[1] - triangle[0]
    )
    points = 0.999 * edge_points + 0.001 * triangle.mean(axis=0)

    np.testing.assert_array_equal(
        Polygon(triangle).contains_points(points), np.ones(19, dtype=bool)
    )


def test_polygon_normalizes_exterior_and_hole_winding() -> None:
    polygon = Polygon(square()[::-1], (square(3.0, 7.0),))

    def signed_area(ring: np.ndarray) -> float:
        shifted = np.roll(ring, -1, axis=0)
        return 0.5 * float(
            np.sum(ring[:, 0] * shifted[:, 1] - shifted[:, 0] * ring[:, 1])
        )

    assert signed_area(polygon.exterior) > 0
    assert signed_area(polygon.holes[0]) < 0


def test_polygon_rejects_invalid_probe_points() -> None:
    polygon = Polygon(square())

    with pytest.raises(ValueError, match=r'shape \(n, 2\)'):
        polygon.contains_points([1.0, 2.0])
    with pytest.raises(ValueError, match='finite'):
        polygon.contains_points([[np.nan, 2.0]])


def test_polygon_set_domain_frame_shape_and_identity() -> None:
    polygons = [Polygon(square()), Polygon(square(20.0, 30.0))]
    domain = PolygonSetDomain(polygons, ('L', 'P'), ('mm', 'mm'))
    other = PolygonSetDomain(polygons, ('L', 'P'), ('mm', 'mm'))

    polygons.clear()
    assert domain.n_elements == 2
    assert domain.shape == (2,)
    assert domain.polygons[0].exterior.shape == (4, 2)
    assert tuple(axis.name for axis in domain.intrinsic_frame.axes) == (
        'L',
        'P',
    )
    assert tuple(axis.unit for axis in domain.intrinsic_frame.axes) == (
        'mm',
        'mm',
    )
    assert domain != other
    assert len({domain, other}) == 2
    with pytest.raises(FrozenInstanceError):
        domain.axis_names = ('x', 'y')  # type: ignore[misc]


@pytest.mark.parametrize(
    ('polygons', 'axis_names', 'axis_units', 'error', 'message'),
    [
        ((object(),), ('L', 'P'), ('mm', 'mm'), TypeError, 'Polygon'),
        ((), ('L',), ('mm', 'mm'), ValueError, 'exactly two'),
        ((), ('L', 'P'), ('mm',), ValueError, 'exactly two'),
        ((), ('L', 'L'), ('mm', 'mm'), ValueError, 'unique'),
        ((), 'LP', ('mm', 'mm'), TypeError, 'pair of names'),
        ((), ('L', 'P'), 'mm', TypeError, 'pair of units'),
    ],
)
def test_polygon_set_domain_rejects_invalid_construction(
    polygons: tuple[object, ...],
    axis_names: tuple[str, ...],
    axis_units: tuple[str, ...],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        PolygonSetDomain(  # type: ignore[arg-type]
            polygons, axis_names, axis_units
        )


def test_polygon_set_domain_deepcopy_pickle_and_replace() -> None:
    domain = PolygonSetDomain(
        (Polygon(square(), (square(3.0, 7.0),)),),
        ('L', 'P'),
        ('mm', 'mm'),
    )

    copied = deepcopy(domain)
    restored = pickle.loads(pickle.dumps(domain))
    replaced = domain.replace(axis_names=('x', 'y'))

    for candidate in (copied, restored):
        assert candidate is not domain
        assert candidate.polygons[0] is not domain.polygons[0]
        assert not candidate.polygons[0].exterior.flags.writeable
        assert not candidate.polygons[0].holes[0].flags.writeable
        np.testing.assert_array_equal(
            candidate.polygons[0].exterior, domain.polygons[0].exterior
        )
    assert replaced.axis_names == ('x', 'y')
    assert domain.axis_names == ('L', 'P')


def test_polygon_set_domain_union_preserves_island_in_hole() -> None:
    outer = Polygon(square(), (square(2.0, 8.0),))
    island = Polygon(square(4.0, 6.0))
    overlapping_island = Polygon(square(5.0, 7.0))
    domain = PolygonSetDomain(
        (outer, island, overlapping_island), ('L', 'P'), ('mm', 'mm')
    )

    multipolygon = domain.as_multipolygon()

    assert isinstance(multipolygon, ShapelyMultiPolygon)
    assert multipolygon.is_valid
    assert len(multipolygon.geoms) == 2
    # The two island components overlap and are unioned before contributing.
    assert multipolygon.area == pytest.approx(64.0 + 7.0)


def test_polygon_set_domain_round_trips_multipolygon() -> None:
    multipolygon = ShapelyMultiPolygon(
        (
            ShapelyPolygon(square(), (square(2.0, 8.0),)),
            ShapelyPolygon(square(4.0, 6.0)),
        )
    )

    domain = PolygonSetDomain.from_multipolygon(
        multipolygon, ('L', 'P'), ('mm', 'mm')
    )

    assert domain.n_elements == 2
    assert domain.axis_names == ('L', 'P')
    assert domain.axis_units == ('mm', 'mm')
    assert domain.as_multipolygon().equals(multipolygon)


def test_polygon_set_domain_normalizes_overlapping_multipolygon_input() -> (
    None
):
    overlapping = ShapelyMultiPolygon(
        (ShapelyPolygon(square()), ShapelyPolygon(square(5.0, 15.0)))
    )

    domain = PolygonSetDomain.from_multipolygon(overlapping, ('L', 'P'))

    assert domain.n_elements == 2
    assert domain.as_multipolygon().is_valid
    assert len(domain.as_multipolygon().geoms) == 1
    assert domain.as_multipolygon().area == pytest.approx(175.0)


def test_polygon_set_domain_rejects_non_multipolygon_input() -> None:
    with pytest.raises(TypeError, match=r'shapely\.MultiPolygon'):
        PolygonSetDomain.from_multipolygon(  # type: ignore[arg-type]
            ShapelyPolygon(square()), ('L', 'P')
        )


def test_data_object_accepts_polygon_fields_and_empty_selection() -> None:
    domain = PolygonSetDomain(
        (Polygon(square()), Polygon(square(20.0, 30.0))),
        ('L', 'P'),
        ('mm', 'mm'),
    )
    labels = np.array(['lesion', 'control'])
    t2star = np.array([18.5, 24.0])
    data = DataObject(
        'ROIs',
        domain,
        {
            'label': Field(
                'label',
                ArraySource(labels),
                interpolation=Interpolation.NEAREST,
            ),
            't2star_ms': Field('t2star_ms', ArraySource(t2star), unit='ms'),
        },
    )

    assert data.domain is domain
    assert data.fields['label'].shape == (2,)
    np.testing.assert_array_equal(
        CoordinateSelection({}).read(data, 'label'), labels
    )
    with pytest.raises(TypeError, match='domain with axes'):
        CoordinateSelection({'element': 0}).apply(data)


def test_data_object_rejects_polygon_field_with_wrong_element_count() -> None:
    domain = PolygonSetDomain((Polygon(square()),), ('L', 'P'))
    field = Field('label', ArraySource(np.array(['one', 'two'])))

    with pytest.raises(ValueError, match='does not match domain shape'):
        DataObject('ROIs', domain, {'label': field})


def test_plane_anchor_copies_context_and_validates_axes() -> None:
    frame = CoordinateFrame('LPS', (('L', 'mm'), ('P', 'mm'), ('S', 'mm')))
    context = {'EchoTime': 9.84}
    anchor = PlaneAnchor(frame, 'S', 12.0, ('L', 'P'), context)
    context.clear()

    assert anchor.context == {'EchoTime': 9.84}
    with pytest.raises(TypeError):
        anchor.context['TimePoint'] = 3  # type: ignore[index]
    with pytest.raises(ValueError, match='plane_axis'):
        PlaneAnchor(frame, 'z', 12.0, ('L', 'P'))
    with pytest.raises(ValueError, match='exactly two'):
        PlaneAnchor(frame, 'S', 12.0, ('L',))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match='pair of axis names'):
        PlaneAnchor(frame, 'S', 12.0, 'LP')  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='distinct'):
        PlaneAnchor(frame, 'S', 12.0, ('L', 'L'))
    with pytest.raises(ValueError, match='disjoint'):
        PlaneAnchor(frame, 'S', 12.0, ('L', 'S'))
    with pytest.raises(ValueError, match='context axes'):
        PlaneAnchor(frame, 'S', 12.0, ('L', 'P'), {'L': 2.0})


def test_roi_matching_uses_absolute_tolerance_and_context() -> None:
    roi = make_roi(make_target(), context={'EchoTime': 9.84, 'TimePoint': 3})
    equivalent_frame = CoordinateFrame(
        'equivalent',
        tuple((axis.name, axis.unit) for axis in roi.anchor.frame.axes),
    )

    assert roi.matches_context(
        {'EchoTime': 9.84, 'TimePoint': 3, 'Coil': 1},
        frame=roi.anchor.frame,
    )
    assert not roi.matches_context({'EchoTime': 9.84}, frame=roi.anchor.frame)
    assert not roi.matches_context(
        {'EchoTime': 9.84, 'TimePoint': 3}, frame=equivalent_frame
    )
    assert roi.matches(
        12.5,
        {'EchoTime': 9.84, 'TimePoint': 3, 'Coil': 1},
        tolerance=0.5,
    )
    assert not roi.matches(
        12.5001,
        {'EchoTime': 9.84, 'TimePoint': 3},
        tolerance=0.5,
    )
    assert not roi.matches(12.0, {'EchoTime': 9.84}, tolerance=0.5)
    assert not roi.matches(
        12.0,
        {'EchoTime': 9.84, 'TimePoint': 4},
        tolerance=0.5,
    )
    with pytest.raises(TypeError, match='tolerance'):
        roi.matches(12.0, {}, tolerance=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='non-negative'):
        roi.matches(12.0, {}, tolerance=-0.1)


def test_roi_matching_uses_relative_float_context_tolerance() -> None:
    roi = make_roi(make_target(), context={'EchoTime': 1000.0, 'Cycle': 3})

    assert roi.matches(
        12.0,
        {'EchoTime': 1000.0 + 9e-7, 'Cycle': 3},
        tolerance=0.0,
    )
    assert not roi.matches(
        12.0,
        {'EchoTime': 1000.0 + 2e-6, 'Cycle': 3},
        tolerance=0.0,
    )
    assert not roi.matches(12.0, {'EchoTime': 1000.0}, tolerance=0.0)


def test_roi_with_empty_context_applies_at_every_context_position() -> None:
    roi = make_roi(make_target())

    assert roi.matches(12.0, {}, tolerance=0.0)
    assert roi.matches(12.0, {'EchoTime': 123.0}, tolerance=0.0)


def test_roi_rejects_geometry_anchor_axis_mismatch() -> None:
    target = make_target()
    data = DataObject(
        'ROI data',
        PolygonSetDomain((Polygon(square()),), ('P', 'L'), ('mm', 'mm')),
    )
    frame = CoordinateFrame('LPS', (('L', 'mm'), ('P', 'mm'), ('S', 'mm')))
    anchor = PlaneAnchor(frame, 'S', 12.0, ('L', 'P'))

    with pytest.raises(ValueError, match='match anchor'):
        ROI('lesion', data, anchor, target)


def test_roi_collection_orders_filters_and_notifies() -> None:
    target = make_target()
    first = make_roi(
        target,
        name='first',
        plane_value=12.0,
        context={'EchoTime': 9.84},
    )
    second = make_roi(target, name='second', plane_value=13.0)
    collection = ROICollection(target)
    changes: list[tuple[str, ...]] = []
    collection.on_changed.append(
        lambda: changes.append(tuple(roi.name for roi in collection))
    )

    collection.add(first)
    collection.add(second)

    assert tuple(collection) == (first, second)
    assert collection.visible_at(
        'S', 12.4, {'EchoTime': 9.84}, tolerance=0.5
    ) == (first,)
    assert collection.visible_at(
        'S', 12.6, {'EchoTime': 7.0}, tolerance=0.5
    ) == (second,)
    assert (
        collection.visible_at('L', 12.0, {'EchoTime': 9.84}, tolerance=0.5)
        == ()
    )

    collection.remove(first)
    assert changes == [('first',), ('first', 'second'), ('second',)]


def test_roi_collection_filters_equivalent_frames_by_identity() -> None:
    target = make_target()
    roi = make_roi(target)
    equivalent_frame = CoordinateFrame(
        'LPS', (('L', 'mm'), ('P', 'mm'), ('S', 'mm'))
    )
    collection = ROICollection(target)
    collection.add(roi)

    assert collection.visible_at(
        'S',
        12.0,
        {},
        tolerance=0.0,
        frame=roi.anchor.frame,
    ) == (roi,)
    assert (
        collection.visible_at(
            'S', 12.0, {}, tolerance=0.0, frame=equivalent_frame
        )
        == ()
    )


def test_roi_collection_rejects_a_different_target() -> None:
    collection = ROICollection(make_target('first target'))
    roi = make_roi(make_target('second target'))

    with pytest.raises(ValueError, match='collection target'):
        collection.add(roi)


@pytest.mark.parametrize(
    'round_trip',
    [deepcopy, lambda value: pickle.loads(pickle.dumps(value))],
)
def test_roi_collection_deepcopy_and_pickle_preserve_attachment_graph(
    round_trip: object,
) -> None:
    target = make_target()
    collection = ROICollection(target)
    collection.add(make_roi(target))

    restored = round_trip(collection)  # type: ignore[operator]
    restored_roi = next(iter(restored))

    assert restored is not collection
    assert restored.target is restored_roi.target
    assert restored.target is not target
    assert not restored_roi.data.domain.polygons[0].exterior.flags.writeable
