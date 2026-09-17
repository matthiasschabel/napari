from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from napari.experimental._data_model import (
    AffineMapping,
    CoordinateAxis,
    CoordinateEmbedding,
    CoordinateFrame,
    CoordinateGraph,
    CorrespondenceBasis,
    DataObject,
    FrameRegistry,
    MappingEdge,
    Polygon,
    ResolvedPath,
    StructuredGridDomain,
    Unplaced,
)

_ATOL = 1e-12


class _NonlinearTransform:
    def __init__(
        self, source: CoordinateFrame, target: CoordinateFrame
    ) -> None:
        self.source_frame = source
        self.target_frame = target

    def map_points(self, points: Any, *, frame: CoordinateFrame) -> Any:
        return points

    def inverse(self) -> _NonlinearTransform:
        raise NotImplementedError


class _NonProtocolInverseAffine(AffineMapping):
    def inverse(self) -> Any:
        return object()


class _InconsistentInverseAffine(AffineMapping):
    def inverse(self) -> AffineMapping:
        return AffineMapping(
            self.source_frame,
            self.target_frame,
            np.eye(self.source_frame.ndim),
        )


class _ValueErrorInverseAffine(AffineMapping):
    def inverse(self) -> AffineMapping:
        raise ValueError('inverse implementation failed')


def _frame(name: str, ndim: int = 2) -> CoordinateFrame:
    axes = tuple((f'x{index}', 'mm') for index in range(ndim))
    return CoordinateFrame(name, axes)


def _edge(
    source: CoordinateFrame,
    target: CoordinateFrame,
    *,
    matrix: Any | None = None,
    offset: Any | None = None,
    basis: CorrespondenceBasis = CorrespondenceBasis.CALIBRATION,
    accuracy: float | None = 1.0,
    validity_region: Any | None = None,
) -> MappingEdge:
    if matrix is None:
        matrix = np.eye(target.ndim, source.ndim)
    return MappingEdge(
        AffineMapping(source, target, matrix, offset),
        basis,
        accuracy=accuracy,
        validity_region=validity_region,
    )


def test_registry_preserves_identity_and_rejects_axis_collision() -> None:
    registry = FrameRegistry()
    axes = (('L', 'mm'), ('P', 'mm'), ('S', 'mm'))

    first = registry.get_or_create('LPS:1.2.3', axes)
    second = registry.get_or_create('LPS:1.2.3', axes)

    assert second is first
    assert registry.frames['LPS:1.2.3'] is first
    with pytest.raises(ValueError, match='different axes'):
        registry.get_or_create(
            'LPS:1.2.3', (('L', 'mm'), ('P', 'mm'), ('S', 'cm'))
        )


@pytest.mark.parametrize(
    ('basis', 'role'),
    [
        (CorrespondenceBasis.SAME_PHYSICAL_OBJECT, 'correction'),
        (CorrespondenceBasis.CALIBRATION, 'correction'),
        (CorrespondenceBasis.SAME_SUBJECT_OVER_TIME, 'correspondence'),
        (CorrespondenceBasis.INTRA_SPECIES_HOMOLOGY, 'correspondence'),
        (CorrespondenceBasis.CROSS_SPECIES_HOMOLOGY, 'correspondence'),
        (CorrespondenceBasis.ATLAS_BY_CONSTRUCTION, 'correspondence'),
        (CorrespondenceBasis.USER_DECLARED, 'composition'),
        (CorrespondenceBasis.OTHER, 'composition'),
    ],
)
def test_edge_role_is_derived_from_basis(
    basis: CorrespondenceBasis, role: str
) -> None:
    edge = _edge(
        _frame('source'), _frame('target'), basis=basis, accuracy=None
    )

    assert edge.role == role
    assert basis.role == role


def test_edge_is_frozen_and_exposes_transform_frames() -> None:
    source = _frame('source')
    target = _frame('target')
    transform = AffineMapping(source, target, np.eye(2))
    edge = MappingEdge(transform, CorrespondenceBasis.CALIBRATION)

    assert edge.transform is transform
    assert edge.source_frame is source
    assert edge.target_frame is target
    with pytest.raises(FrozenInstanceError):
        edge.accuracy = 2.0  # type: ignore[misc]


def test_edge_rejects_invalid_transform_and_frames() -> None:
    with pytest.raises(TypeError, match='CoordinateTransform protocol'):
        MappingEdge(object(), CorrespondenceBasis.CALIBRATION)  # type: ignore[arg-type]

    frame = _frame('same')
    with pytest.raises(ValueError, match='must be distinct'):
        MappingEdge(
            AffineMapping(frame, frame, np.eye(2)),
            CorrespondenceBasis.CALIBRATION,
        )

    invalid_frames = SimpleNamespace(
        source_frame=object(),
        target_frame=object(),
        map_points=lambda points, *, frame: points,
        inverse=lambda: None,
    )
    with pytest.raises(TypeError, match='must be CoordinateFrame'):
        MappingEdge(invalid_frames, CorrespondenceBasis.CALIBRATION)


def test_non_affine_edge_is_unusable_in_both_directions() -> None:
    source = _frame('source')
    target = _frame('target')
    edge = MappingEdge(
        _NonlinearTransform(source, target),
        CorrespondenceBasis.CALIBRATION,
    )
    graph = CoordinateGraph()
    graph.add(edge)

    assert graph.resolve(source, target) == Unplaced(source, target)
    assert graph.resolve(target, source) == Unplaced(target, source)


def test_edge_rejects_invalid_basis_and_provenance() -> None:
    transform = AffineMapping(_frame('source'), _frame('target'), np.eye(2))

    with pytest.raises(TypeError, match='CorrespondenceBasis'):
        MappingEdge(transform, 'calibration')  # type: ignore[arg-type]
    with pytest.raises(TypeError, match='provenance'):
        MappingEdge(
            transform,
            CorrespondenceBasis.CALIBRATION,
            provenance=object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ('accuracy', 'error', 'match'),
    [
        (True, TypeError, 'real number'),
        (-0.1, ValueError, 'non-negative'),
        (np.inf, ValueError, 'finite'),
        (np.nan, ValueError, 'finite'),
    ],
)
def test_edge_rejects_invalid_accuracy(
    accuracy: Any, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        _edge(_frame('source'), _frame('target'), accuracy=accuracy)


def test_user_declared_edge_rejects_accuracy() -> None:
    with pytest.raises(ValueError, match='cannot carry accuracy'):
        _edge(
            _frame('source'),
            _frame('target'),
            basis=CorrespondenceBasis.USER_DECLARED,
            accuracy=0.0,
        )


def test_edge_normalizes_bounds_and_accepts_polygon() -> None:
    source = _frame('source')
    target = _frame('target')
    bounds = ((0, 1), (10, 20))
    edge = _edge(source, target, validity_region=bounds)
    polygon = Polygon(np.array([[0, 0], [2, 0], [0, 2]]))
    polygon_edge = _edge(source, target, validity_region=polygon)

    assert edge.validity_region == ((0.0, 1.0), (10.0, 20.0))
    assert polygon_edge.validity_region is polygon


@pytest.mark.parametrize(
    ('region', 'error', 'match'),
    [
        ('invalid', TypeError, 'lower, upper'),
        (b'invalid', TypeError, 'lower, upper'),
        (object(), TypeError, 'Polygon, bounds pair, or None'),
        (((0.0, 1.0),), ValueError, 'lower and upper coordinates'),
        ((0.0, 1.0), TypeError, 'coordinates must be sequences'),
        (((0.0,), (1.0,)), ValueError, 'target frame dimension'),
        (((0.0, 1.0, 2.0), (3.0, 4.0, 5.0)), ValueError, 'dimension'),
        (((0.0, 1.0), (2.0, np.inf)), ValueError, 'finite'),
        (((0.0, 2.0), (1.0, 1.0)), ValueError, 'must not exceed'),
        (((0.0, 'a'), (1.0, 2.0)), TypeError, 'real numbers'),
    ],
)
def test_edge_rejects_invalid_bounds(
    region: Any, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        _edge(_frame('source'), _frame('target'), validity_region=region)


def test_edge_rejects_polygon_for_non_2d_target() -> None:
    polygon = Polygon(np.array([[0, 0], [2, 0], [0, 2]]))

    with pytest.raises(ValueError, match='2-D target'):
        _edge(
            _frame('source', 3),
            _frame('target', 3),
            validity_region=polygon,
        )


def test_worlds_are_derived_from_current_edges() -> None:
    graph = CoordinateGraph()
    a, b, c, d = (_frame(name) for name in ('A', 'B', 'C', 'D'))
    ab = _edge(a, b)
    cd = _edge(c, d)
    bridge = _edge(b, c)

    graph.add(ab)
    graph.add(cd)
    assert graph.frames == (a, b, c, d)
    assert graph.worlds() == (frozenset((a, b)), frozenset((c, d)))

    graph.add(bridge)
    assert graph.worlds() == (frozenset((a, b, c, d)),)

    graph.remove(bridge)
    assert graph.worlds() == (frozenset((a, b)), frozenset((c, d)))
    graph.remove(cd)
    assert graph.frames == (a, b)
    assert graph.worlds() == (frozenset((a, b)),)


def test_worlds_empty_graph_has_no_components() -> None:
    assert CoordinateGraph().worlds() == ()


def test_worlds_include_edgeless_frames_and_world_of_returns_component() -> (
    None
):
    graph = CoordinateGraph()
    a, b, isolated = (_frame(name) for name in ('A', 'B', 'C'))
    graph.add(_edge(a, b))

    assert graph.worlds(include=(isolated, a)) == (
        frozenset((a, b)),
        frozenset((isolated,)),
    )
    assert graph.world_of(a) == frozenset((a, b))
    assert graph.world_of(isolated) == frozenset((isolated,))


def test_graph_rejects_invalid_duplicate_and_missing_edges() -> None:
    graph = CoordinateGraph()
    edge = _edge(_frame('A'), _frame('B'))

    with pytest.raises(TypeError, match='MappingEdge'):
        graph.add(object())  # type: ignore[arg-type]
    graph.add(edge)
    with pytest.raises(ValueError, match='already'):
        graph.add(edge)
    graph.remove(edge)
    with pytest.raises(ValueError, match='not in'):
        graph.remove(edge)
    with pytest.raises(TypeError, match='MappingEdge'):
        graph.remove(object())  # type: ignore[arg-type]


def test_resolve_identity_is_neutral_correction() -> None:
    frame = _frame('A')

    result = CoordinateGraph().resolve(frame, frame)

    assert isinstance(result, ResolvedPath)
    assert result.edges == ()
    assert result.role == 'correction'
    assert result.accuracy == 0.0
    assert result.validity_regions == ()
    assert result.reversed_edges == ()
    assert result.transform is result.mapping
    assert result.mapping.source_frame is frame
    assert result.mapping.target_frame is frame
    np.testing.assert_allclose(
        result.mapping.map_points([[2.0, 3.0]], frame=frame),
        [[2.0, 3.0]],
        rtol=0.0,
        atol=_ATOL,
    )


def test_resolve_prefers_direct_edge_and_can_reverse_it() -> None:
    graph = CoordinateGraph()
    source = _frame('A')
    target = _frame('B')
    direct = _edge(
        source,
        target,
        matrix=[[2.0, 0.0], [0.0, 4.0]],
        offset=[10.0, -8.0],
        accuracy=0.5,
    )
    graph.add(direct)

    forward = graph.resolve(source, target)
    reverse = graph.resolve(target, source)

    assert isinstance(forward, ResolvedPath)
    assert isinstance(reverse, ResolvedPath)
    assert forward.edges == (direct,)
    assert reverse.edges == (direct,)
    assert forward.reversed_edges == (False,)
    assert reverse.reversed_edges == (True,)
    np.testing.assert_allclose(
        forward.mapping.map_points([[1.0, 2.0]], frame=source),
        [[12.0, 0.0]],
        rtol=0.0,
        atol=_ATOL,
    )
    np.testing.assert_allclose(
        reverse.mapping.map_points([[12.0, 0.0]], frame=target),
        [[1.0, 2.0]],
        rtol=0.0,
        atol=_ATOL,
    )


def test_noninvertible_reverse_is_unplaced() -> None:
    source = _frame('A')
    target = _frame('B')
    graph = CoordinateGraph()
    graph.add(_edge(source, target, matrix=[[1.0, 2.0], [2.0, 4.0]]))

    result = graph.resolve(target, source)

    assert result == Unplaced(target, source)


def test_inverse_protocol_rejects_non_transform_result() -> None:
    source = _frame('A')
    target = _frame('B')
    graph = CoordinateGraph()
    graph.add(
        MappingEdge(
            _NonProtocolInverseAffine(source, target, np.eye(2)),
            CorrespondenceBasis.CALIBRATION,
        )
    )

    with pytest.raises(TypeError, match='must satisfy CoordinateTransform'):
        graph.resolve(target, source)


def test_inverse_protocol_rejects_inconsistent_frames() -> None:
    source = _frame('A')
    target = _frame('B')
    graph = CoordinateGraph()
    graph.add(
        MappingEdge(
            _InconsistentInverseAffine(source, target, np.eye(2)),
            CorrespondenceBasis.CALIBRATION,
        )
    )

    with pytest.raises(ValueError, match='inconsistent frames'):
        graph.resolve(target, source)


def test_inverse_value_error_propagates() -> None:
    source = _frame('A')
    target = _frame('B')
    graph = CoordinateGraph()
    graph.add(
        MappingEdge(
            _ValueErrorInverseAffine(source, target, np.eye(2)),
            CorrespondenceBasis.CALIBRATION,
        )
    )

    with pytest.raises(ValueError, match='inverse implementation failed'):
        graph.resolve(target, source)


def test_resolve_two_hop_hub_route_composes_reverse_edge() -> None:
    a = _frame('A')
    b = _frame('B')
    mni = _frame('MNI152')
    a_to_mni = _edge(
        a,
        mni,
        matrix=2.0 * np.eye(2),
        offset=[10.0, 20.0],
        basis=CorrespondenceBasis.ATLAS_BY_CONSTRUCTION,
    )
    b_to_mni = _edge(
        b,
        mni,
        matrix=4.0 * np.eye(2),
        offset=[2.0, 8.0],
        basis=CorrespondenceBasis.INTRA_SPECIES_HOMOLOGY,
    )
    graph = CoordinateGraph()
    graph.add(a_to_mni)
    graph.add(b_to_mni)

    result = graph.resolve(a, b)

    assert isinstance(result, ResolvedPath)
    assert result.edges == (a_to_mni, b_to_mni)
    assert result.reversed_edges == (False, True)
    assert result.role == 'correspondence'
    np.testing.assert_allclose(
        result.mapping.matrix, 0.5 * np.eye(2), rtol=0.0, atol=_ATOL
    )
    np.testing.assert_allclose(
        result.mapping.offset, [2.0, 3.0], rtol=0.0, atol=_ATOL
    )


def test_resolve_prefers_direct_edge_over_multihop_route() -> None:
    a, b, hub = (_frame(name) for name in ('A', 'B', 'Hub'))
    direct = _edge(a, b, offset=[100.0, 100.0])
    graph = CoordinateGraph()
    graph.add(_edge(a, hub, offset=[1.0, 1.0]))
    graph.add(_edge(hub, b, offset=[2.0, 2.0]))
    graph.add(direct)

    result = graph.resolve(a, b)

    assert isinstance(result, ResolvedPath)
    assert result.edges == (direct,)


@pytest.mark.parametrize('calibration_first', [False, True])
def test_parallel_direct_edges_prefer_role_in_both_insertion_orders(
    calibration_first: bool,
) -> None:
    source = _frame('A')
    target = _frame('B')
    fiat = _edge(
        source,
        target,
        basis=CorrespondenceBasis.USER_DECLARED,
        accuracy=None,
    )
    calibration = _edge(
        source,
        target,
        basis=CorrespondenceBasis.CALIBRATION,
        accuracy=10.0,
    )
    graph = CoordinateGraph()
    edges = (calibration, fiat) if calibration_first else (fiat, calibration)
    for edge in edges:
        graph.add(edge)

    result = graph.resolve(source, target)

    assert isinstance(result, ResolvedPath)
    assert result.edges == (calibration,)


def test_parallel_direct_edges_use_accuracy_then_insertion_order() -> None:
    source = _frame('A')
    target = _frame('B')
    unknown_accuracy = _edge(source, target, accuracy=None)
    less_accurate = _edge(source, target, accuracy=2.0)
    more_accurate = _edge(source, target, accuracy=0.5)
    graph = CoordinateGraph()
    for edge in (unknown_accuracy, less_accurate, more_accurate):
        graph.add(edge)

    result = graph.resolve(source, target)

    assert isinstance(result, ResolvedPath)
    assert result.edges == (more_accurate,)

    equally_accurate = _edge(source, target, accuracy=0.5)
    graph.add(equally_accurate)
    result = graph.resolve(source, target)
    assert isinstance(result, ResolvedPath)
    assert result.edges == (more_accurate,)


def test_resolve_shortest_path_uses_lexicographic_frame_tie_break() -> None:
    a, x, y, z = (_frame(name) for name in ('A', 'X', 'Y', 'Z'))
    ay = _edge(a, y)
    yz = _edge(y, z)
    ax = _edge(a, x)
    xz = _edge(x, z)
    graph = CoordinateGraph()
    for edge in (ay, yz, ax, xz):
        graph.add(edge)

    result = graph.resolve(a, z)

    assert isinstance(result, ResolvedPath)
    assert result.edges == (ax, xz)


def test_resolve_uses_multihop_when_direct_reverse_is_not_invertible() -> None:
    a, b, hub = (_frame(name) for name in ('A', 'B', 'Hub'))
    graph = CoordinateGraph()
    graph.add(_edge(b, a, matrix=[[1.0, 2.0], [2.0, 4.0]]))
    a_to_hub = _edge(a, hub)
    hub_to_b = _edge(hub, b)
    graph.add(a_to_hub)
    graph.add(hub_to_b)

    result = graph.resolve(a, b)

    assert isinstance(result, ResolvedPath)
    assert result.edges == (a_to_hub, hub_to_b)


def test_resolve_routes_around_non_affine_edge() -> None:
    a, b, hub = (_frame(name) for name in ('A', 'B', 'Hub'))
    graph = CoordinateGraph()
    graph.add(
        MappingEdge(_NonlinearTransform(a, b), CorrespondenceBasis.CALIBRATION)
    )
    a_to_hub = _edge(a, hub)
    hub_to_b = _edge(hub, b)
    graph.add(a_to_hub)
    graph.add(hub_to_b)

    result = graph.resolve(a, b)

    assert isinstance(result, ResolvedPath)
    assert result.edges == (a_to_hub, hub_to_b)


def test_resolve_returns_typed_unplaced_result() -> None:
    source = _frame('A')
    target = _frame('B')

    result = CoordinateGraph().resolve(source, target)

    assert isinstance(result, Unplaced)
    assert result.source_frame is source
    assert result.target_frame is target


def test_resolve_between_populated_disconnected_worlds_is_unplaced() -> None:
    a, b, c, d = (_frame(name) for name in ('A', 'B', 'C', 'D'))
    graph = CoordinateGraph()
    graph.add(_edge(a, b))
    graph.add(_edge(c, d))

    assert graph.resolve(a, c) == Unplaced(a, c)


def test_resolve_rejects_non_frame_inputs() -> None:
    frame = _frame('A')
    graph = CoordinateGraph()

    with pytest.raises(TypeError, match='source must be'):
        graph.resolve(object(), frame)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match='target must be'):
        graph.resolve(frame, object())  # type: ignore[arg-type]


def test_resolve_composes_accuracy_with_rss_and_propagates_none() -> None:
    a, b, c = (_frame(name) for name in ('A', 'B', 'C'))
    graph = CoordinateGraph()
    graph.add(_edge(a, b, accuracy=3.0))
    graph.add(_edge(b, c, accuracy=4.0))

    result = graph.resolve(a, c)

    assert isinstance(result, ResolvedPath)
    assert result.accuracy == pytest.approx(5.0, abs=_ATOL)

    graph = CoordinateGraph()
    graph.add(_edge(a, b, accuracy=3.0))
    graph.add(_edge(b, c, accuracy=None))
    result = graph.resolve(a, c)
    assert isinstance(result, ResolvedPath)
    assert result.accuracy is None


def test_resolve_uses_weakest_role_and_carries_validity_regions() -> None:
    a, b, c, d = (_frame(name) for name in ('A', 'B', 'C', 'D'))
    correction_region = ((0.0, 0.0), (10.0, 10.0))
    correction = _edge(
        a,
        b,
        basis=CorrespondenceBasis.CALIBRATION,
        validity_region=correction_region,
    )
    correspondence = _edge(
        b,
        c,
        basis=CorrespondenceBasis.SAME_SUBJECT_OVER_TIME,
        validity_region=None,
    )
    fiat_region = ((-1.0, -2.0), (1.0, 2.0))
    fiat = _edge(
        c,
        d,
        basis=CorrespondenceBasis.USER_DECLARED,
        accuracy=None,
        validity_region=fiat_region,
    )
    graph = CoordinateGraph()
    for edge in (correction, correspondence, fiat):
        graph.add(edge)

    result = graph.resolve(a, d)

    assert isinstance(result, ResolvedPath)
    assert result.role == 'composition'
    assert result.validity_regions == (
        correction_region,
        None,
        fiat_region,
    )


def test_resolve_object_composes_embedding_then_graph_path() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    acquisition = _frame('acquisition')
    view = _frame('view')
    embedding = CoordinateEmbedding(
        domain,
        acquisition,
        [[2.0, 0.5], [-1.0, 3.0]],
        [10.0, -4.0],
    )
    obj = DataObject('image', domain, embeddings=(embedding,))
    edge = _edge(
        acquisition,
        view,
        matrix=[[0.0, 2.0], [4.0, 0.0]],
        offset=[5.0, 7.0],
        accuracy=0.25,
    )
    graph = CoordinateGraph()
    graph.add(edge)

    result = graph.resolve_object(obj, view)

    assert isinstance(result, ResolvedPath)
    assert result.edges == (edge,)
    assert result.mapping.source_frame is domain.intrinsic_frame
    assert result.mapping.target_frame is view
    expected_matrix = edge.transform.matrix @ embedding.matrix
    expected_offset = (
        edge.transform.matrix @ embedding.offset + edge.transform.offset
    )
    np.testing.assert_allclose(
        result.mapping.matrix, expected_matrix, rtol=0.0, atol=_ATOL
    )
    np.testing.assert_allclose(
        result.mapping.offset, expected_offset, rtol=0.0, atol=_ATOL
    )
    point = np.array([[1.5, -2.0]])
    expected_point = point @ expected_matrix.T + expected_offset
    np.testing.assert_allclose(
        result.mapping.map_points(point, frame=domain.intrinsic_frame),
        expected_point,
        rtol=0.0,
        atol=_ATOL,
    )


def test_resolve_object_without_embedding_uses_intrinsic_frame() -> None:
    domain = StructuredGridDomain((CoordinateAxis('x', 2),))
    obj = DataObject('line', domain)

    result = CoordinateGraph().resolve_object(obj, domain.intrinsic_frame)

    assert isinstance(result, ResolvedPath)
    assert result.mapping.source_frame is domain.intrinsic_frame
    assert result.mapping.target_frame is domain.intrinsic_frame


def test_resolve_object_returns_unplaced_from_embedding_frame() -> None:
    domain = StructuredGridDomain((CoordinateAxis('x', 2),))
    acquisition = _frame('acquisition', 1)
    view = _frame('view', 1)
    obj = DataObject(
        'line',
        domain,
        embeddings=(CoordinateEmbedding(domain, acquisition, [[2.0]]),),
    )

    result = CoordinateGraph().resolve_object(obj, view)

    assert isinstance(result, Unplaced)
    assert result.source_frame is domain.intrinsic_frame
    assert result.target_frame is view


def test_resolve_object_rejects_invalid_object_and_multiple_embeddings() -> (
    None
):
    graph = CoordinateGraph()
    target = _frame('target', 1)
    with pytest.raises(TypeError, match='DataObject'):
        graph.resolve_object(object(), target)  # type: ignore[arg-type]

    domain = StructuredGridDomain((CoordinateAxis('x', 2),))
    first = CoordinateEmbedding(domain, _frame('first', 1), [[1.0]])
    second = CoordinateEmbedding(domain, _frame('second', 1), [[1.0]])
    obj = DataObject('line', domain, embeddings=(first, second))
    with pytest.raises(NotImplementedError, match='multiple embeddings'):
        graph.resolve_object(obj, target)
