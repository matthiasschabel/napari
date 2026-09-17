"""Identity-stable coordinate frames and affine path resolution."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from enum import Enum
from numbers import Real
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import numpy as np

from napari.experimental._data_model._axis import (
    CoordinateFrame,
    FrameAxis,
)
from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._geometry import Polygon
from napari.experimental._data_model._mapping import AffineMapping

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

MappingRole = Literal['composition', 'correspondence', 'correction']
Bounds = tuple[tuple[float, ...], tuple[float, ...]]
ValidityRegion = Polygon | Bounds


class FrameRegistry:
    """Own one identity-stable :class:`CoordinateFrame` per string key.

    Keys are uninterpreted. They may be DICOM FrameOfReferenceUID-derived
    names such as ``"LPS:1.2.840..."`` or well-known names such as
    ``"MNI152"``.
    """

    __slots__ = ('_frames',)

    def __init__(self) -> None:
        self._frames: dict[str, CoordinateFrame] = {}

    @property
    def frames(self) -> MappingProxyType[str, CoordinateFrame]:
        """Registered frames keyed by name as a read-only mapping."""
        return MappingProxyType(self._frames)

    def get_or_create(
        self,
        key: str,
        axes: Sequence[FrameAxis | tuple[str, str | None]],
    ) -> CoordinateFrame:
        """Return the stable frame for ``key``, creating it when absent.

        A key cannot be reused for a different ordered axis structure.
        """
        candidate = CoordinateFrame(key, tuple(axes))
        existing = self._frames.get(key)
        if existing is None:
            self._frames[key] = candidate
            return candidate
        if existing.axes != candidate.axes:
            raise ValueError(
                f'frame key {key!r} is already registered with different axes'
            )
        return existing


class CorrespondenceBasis(Enum):
    """Declared reason that two coordinate frames may be related.

    The basis determines the edge's comparison contract through
    :attr:`role`. Correction-grade edges presume quantitative comparison is
    valid. Correspondence-grade edges model homology and require scientific
    caveats. Composition-grade edges imply no comparability; consumers must
    refuse or explicitly caveat quantitative operations across them.

    ``OTHER`` is the conservative extension point for a basis not yet in this
    vocabulary. Its exact meaning belongs in provenance, and it receives the
    weakest, composition-grade contract until promoted to an explicit member.
    """

    SAME_PHYSICAL_OBJECT = 'same_physical_object'
    SAME_SUBJECT_OVER_TIME = 'same_subject_over_time'
    INTRA_SPECIES_HOMOLOGY = 'intra_species_homology'
    CROSS_SPECIES_HOMOLOGY = 'cross_species_homology'
    ATLAS_BY_CONSTRUCTION = 'atlas_by_construction'
    CALIBRATION = 'calibration'
    USER_DECLARED = 'user_declared'
    OTHER = 'other'

    @property
    def role(self) -> MappingRole:
        """Comparison contract derived from this basis."""
        if self in {
            CorrespondenceBasis.SAME_PHYSICAL_OBJECT,
            CorrespondenceBasis.CALIBRATION,
        }:
            return 'correction'
        if self in {
            CorrespondenceBasis.SAME_SUBJECT_OVER_TIME,
            CorrespondenceBasis.INTRA_SPECIES_HOMOLOGY,
            CorrespondenceBasis.CROSS_SPECIES_HOMOLOGY,
            CorrespondenceBasis.ATLAS_BY_CONSTRUCTION,
        }:
            return 'correspondence'
        return 'composition'


@runtime_checkable
class CoordinateTransform(Protocol):
    """Open transform protocol stored by :class:`MappingEdge`.

    Stage P resolves affine implementations and treats non-affine edges as
    unusable in both directions. The protocol leaves the edge schema open for
    nonlinear transforms in a later stage.

    Implementations signal that inversion is unavailable by raising
    :class:`NotImplementedError`. Other exceptions from :meth:`inverse`
    propagate as transform failures rather than being interpreted as
    non-invertibility.
    """

    source_frame: CoordinateFrame
    target_frame: CoordinateFrame

    def map_points(self, points: Any, *, frame: CoordinateFrame) -> Any:
        """Map points from ``source_frame`` to ``target_frame``."""
        ...

    def inverse(self) -> CoordinateTransform:
        """Return the inverse or raise ``NotImplementedError`` if unavailable."""
        ...


@dataclass(frozen=True, slots=True, eq=False)
class MappingEdge:
    """Semantic coordinate-graph edge around reusable transform math.

    ``accuracy`` is a non-negative scalar in the target frame's spatial-axis
    units. A scalar is meaningful only when those axes share a compatible
    unit. ``validity_region`` is either a 2-D :class:`Polygon` or an
    axis-aligned ``(lower, upper)`` bounds pair in target-frame coordinates;
    ``None`` means the transform is valid everywhere.
    """

    transform: CoordinateTransform
    basis: CorrespondenceBasis
    accuracy: float | None = None
    validity_region: ValidityRegion | None = None
    provenance: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.transform, CoordinateTransform):
            raise TypeError(
                'transform must satisfy the CoordinateTransform protocol'
            )
        source = self.transform.source_frame
        target = self.transform.target_frame
        if not isinstance(source, CoordinateFrame) or not isinstance(
            target, CoordinateFrame
        ):
            raise TypeError(
                'transform source and target must be CoordinateFrame instances'
            )
        if source is target:
            raise ValueError(
                'mapping edge source and target frames must be distinct'
            )
        if not isinstance(self.basis, CorrespondenceBasis):
            raise TypeError('basis must be a CorrespondenceBasis')
        if self.accuracy is not None:
            if isinstance(self.accuracy, bool) or not isinstance(
                self.accuracy, Real
            ):
                raise TypeError('accuracy must be a real number or None')
            accuracy = float(self.accuracy)
            if not math.isfinite(accuracy) or accuracy < 0.0:
                raise ValueError('accuracy must be finite and non-negative')
            object.__setattr__(self, 'accuracy', accuracy)
        if (
            self.basis is CorrespondenceBasis.USER_DECLARED
            and self.accuracy is not None
        ):
            raise ValueError('user-declared edges cannot carry accuracy')
        if self.provenance is not None and not isinstance(
            self.provenance, str
        ):
            raise TypeError('provenance must be a string or None')
        if self.validity_region is not None:
            object.__setattr__(
                self,
                'validity_region',
                _validate_validity_region(self.validity_region, target),
            )

    @property
    def role(self) -> MappingRole:
        """Comparison contract derived from :attr:`basis`."""
        return self.basis.role

    @property
    def source_frame(self) -> CoordinateFrame:
        """Declared transform source frame."""
        return self.transform.source_frame

    @property
    def target_frame(self) -> CoordinateFrame:
        """Declared transform target frame."""
        return self.transform.target_frame


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """A usable ordered path and its composed affine mapping.

    The path role is its weakest edge contract. Accuracy is the root-sum-square
    of edge accuracies, or ``None`` when any edge has no estimate. This simple
    rule assumes independent scalar errors in compatible units; it does not
    propagate covariance or account for local scaling. ``validity_regions``
    carries each edge's region unchanged, in that edge's declared target-frame
    coordinates. Region transformation and intersection are out of scope.
    Each item in ``reversed_edges`` states whether the corresponding edge was
    traversed from its declared target frame to its declared source frame.
    """

    edges: tuple[MappingEdge, ...]
    mapping: AffineMapping
    role: MappingRole
    accuracy: float | None
    validity_regions: tuple[ValidityRegion | None, ...]
    reversed_edges: tuple[bool, ...]

    @property
    def transform(self) -> AffineMapping:
        """Alias for the composed :attr:`mapping`."""
        return self.mapping


@dataclass(frozen=True, slots=True)
class Unplaced:
    """Expected resolution result when no usable coordinate path exists."""

    source_frame: CoordinateFrame
    target_frame: CoordinateFrame
    reason: str = 'no usable coordinate path'


Resolution = ResolvedPath | Unplaced


class CoordinateGraph:
    """Mutable graph of semantic mappings between coordinate frames.

    Worlds are never stored: :meth:`worlds` derives undirected connected
    components from the current edges. Resolution prefers a usable direct
    edge, then the fewest-hop path. Parallel direct edges are ranked by role,
    accuracy, and insertion order. Equal-hop paths are ordered lexicographically
    by the sequence of frame names; edge insertion order is used only when name
    sequences are identical.
    """

    __slots__ = ('_edges',)

    def __init__(self) -> None:
        self._edges: list[MappingEdge] = []

    @property
    def edges(self) -> tuple[MappingEdge, ...]:
        """Current edges in insertion order."""
        return tuple(self._edges)

    @property
    def frames(self) -> tuple[CoordinateFrame, ...]:
        """Current edge endpoints in first-seen order."""
        frames: list[CoordinateFrame] = []
        seen: set[CoordinateFrame] = set()
        for edge in self._edges:
            for frame in (edge.source_frame, edge.target_frame):
                if frame not in seen:
                    seen.add(frame)
                    frames.append(frame)
        return tuple(frames)

    def add(self, edge: MappingEdge) -> None:
        """Add an edge to the graph."""
        if not isinstance(edge, MappingEdge):
            raise TypeError('edge must be a MappingEdge')
        if edge in self._edges:
            raise ValueError('edge is already in the graph')
        self._edges.append(edge)

    def remove(self, edge: MappingEdge) -> None:
        """Remove an existing edge from the graph."""
        if not isinstance(edge, MappingEdge):
            raise TypeError('edge must be a MappingEdge')
        try:
            self._edges.remove(edge)
        except ValueError as exc:
            raise ValueError('edge is not in the graph') from exc

    def worlds(
        self, include: Iterable[CoordinateFrame] = ()
    ) -> tuple[frozenset[CoordinateFrame], ...]:
        """Derive undirected connected components from current edges.

        Frames in ``include`` participate even when they are not endpoints of
        any edge, in which case they appear as singleton worlds.
        """
        try:
            included_frames = tuple(include)
        except TypeError as exc:
            raise TypeError(
                'include must be an iterable of CoordinateFrame'
            ) from exc
        if not all(
            isinstance(frame, CoordinateFrame) for frame in included_frames
        ):
            raise TypeError(
                'include must contain only CoordinateFrame instances'
            )

        frames = list(self.frames)
        seen = set(frames)
        for frame in included_frames:
            if frame not in seen:
                seen.add(frame)
                frames.append(frame)
        neighbors: dict[CoordinateFrame, set[CoordinateFrame]] = {
            frame: set() for frame in frames
        }
        for edge in self._edges:
            neighbors[edge.source_frame].add(edge.target_frame)
            neighbors[edge.target_frame].add(edge.source_frame)

        insertion_index = {frame: index for index, frame in enumerate(frames)}
        remaining = set(frames)
        components: list[frozenset[CoordinateFrame]] = []
        while remaining:
            start = min(
                remaining,
                key=lambda frame: (frame.name, insertion_index[frame]),
            )
            component: set[CoordinateFrame] = set()
            pending = [start]
            while pending:
                frame = pending.pop()
                if frame in component:
                    continue
                component.add(frame)
                pending.extend(neighbors[frame] - component)
            remaining.difference_update(component)
            components.append(frozenset(component))

        components.sort(
            key=lambda component: tuple(
                sorted(
                    (frame.name, insertion_index[frame]) for frame in component
                )
            )
        )
        return tuple(components)

    def world_of(self, frame: CoordinateFrame) -> frozenset[CoordinateFrame]:
        """Return ``frame``'s connected component, or its singleton world."""
        _validate_resolution_frame(frame, kind='frame')
        for component in self.worlds():
            if frame in component:
                return component
        return frozenset((frame,))

    def resolve(
        self, source: CoordinateFrame, target: CoordinateFrame
    ) -> Resolution:
        """Resolve ``source`` to ``target`` under the documented path policy.

        A usable direct edge always wins over a multi-hop route. Among parallel
        usable direct edges, resolution prefers the strongest role
        (correction, then correspondence, then composition), the smallest
        non-``None`` accuracy (with ``None`` last), then insertion order.

        Returns a :class:`ResolvedPath` when placement is usable and an
        :class:`Unplaced` value when no path exists or every path requires an
        unavailable inverse. Non-affine edges are unusable in both directions;
        resolving them is future work.
        """
        _validate_resolution_frame(source, kind='source')
        _validate_resolution_frame(target, kind='target')
        if source is target:
            return _identity_path(source)

        direct_candidates: list[
            tuple[
                tuple[int, bool, float, int],
                MappingEdge,
                CoordinateTransform,
                bool,
            ]
        ] = []
        for index, edge in enumerate(self._edges):
            oriented = _orient_edge(edge, source, target)
            if oriented is not None:
                transform, reversed_edge = oriented
                direct_candidates.append(
                    (
                        (
                            -_role_strength(edge.role),
                            edge.accuracy is None,
                            math.inf
                            if edge.accuracy is None
                            else edge.accuracy,
                            index,
                        ),
                        edge,
                        transform,
                        reversed_edge,
                    )
                )
        if direct_candidates:
            _, edge, transform, reversed_edge = min(direct_candidates)
            return _resolved_path(
                source,
                target,
                (edge,),
                (transform,),
                (reversed_edge,),
            )

        path = self._shortest_path(source, target)
        if path is None:
            return Unplaced(source, target)
        edges, transforms, reversed_edges = path
        return _resolved_path(
            source, target, edges, transforms, reversed_edges
        )

    def resolve_object(
        self, obj: DataObject, target: CoordinateFrame
    ) -> Resolution:
        """Compose an object's local embedding with graph frame alignment.

        An object without an embedding resolves from its intrinsic frame.
        Multiple alternative embeddings require a selection policy and are
        deliberately not implemented. The embedding is treated as exact and
        carries no comparison contract; role and accuracy are inherited from
        the graph path alone.
        """
        if not isinstance(obj, DataObject):
            raise TypeError('obj must be a DataObject')
        if len(obj.embeddings) > 1:
            raise NotImplementedError(
                'coordinate-graph resolution does not support multiple embeddings'
            )
        if not obj.embeddings:
            return self.resolve(obj.domain.intrinsic_frame, target)

        embedding = obj.embeddings[0]
        graph_result = self.resolve(embedding.target_frame, target)
        if isinstance(graph_result, Unplaced):
            return Unplaced(
                obj.domain.intrinsic_frame,
                target,
                reason=(
                    'no usable coordinate path from the object embedding frame'
                ),
            )
        mapping = _compose_affine_transforms(
            obj.domain.intrinsic_frame,
            target,
            (embedding, graph_result.mapping),
        )
        return ResolvedPath(
            edges=graph_result.edges,
            mapping=mapping,
            role=graph_result.role,
            accuracy=graph_result.accuracy,
            validity_regions=graph_result.validity_regions,
            reversed_edges=graph_result.reversed_edges,
        )

    def _shortest_path(
        self, source: CoordinateFrame, target: CoordinateFrame
    ) -> (
        tuple[
            tuple[MappingEdge, ...],
            tuple[CoordinateTransform, ...],
            tuple[bool, ...],
        ]
        | None
    ):
        counter = 0
        queue: list[
            tuple[
                int,
                tuple[str, ...],
                tuple[int, ...],
                int,
                CoordinateFrame,
                tuple[MappingEdge, ...],
                tuple[CoordinateTransform, ...],
                tuple[bool, ...],
            ]
        ] = [(0, (source.name,), (), counter, source, (), (), ())]
        settled: set[CoordinateFrame] = set()
        while queue:
            (
                _hop_count,
                frame_names,
                edge_indices,
                _counter,
                frame,
                edges,
                transforms,
                reversed_edges,
            ) = heapq.heappop(queue)
            if frame in settled:
                continue
            settled.add(frame)
            if frame is target:
                return edges, transforms, reversed_edges

            neighbors: list[
                tuple[
                    str,
                    int,
                    CoordinateFrame,
                    MappingEdge,
                    CoordinateTransform,
                    bool,
                ]
            ] = []
            for index, edge in enumerate(self._edges):
                oriented = _orient_from(edge, frame)
                if oriented is None:
                    continue
                next_frame, transform, reversed_edge = oriented
                if next_frame in settled:
                    continue
                neighbors.append(
                    (
                        next_frame.name,
                        index,
                        next_frame,
                        edge,
                        transform,
                        reversed_edge,
                    )
                )
            neighbors.sort(key=lambda item: (item[0], item[1]))
            for (
                name,
                index,
                next_frame,
                edge,
                transform,
                reversed_edge,
            ) in neighbors:
                counter += 1
                heapq.heappush(
                    queue,
                    (
                        len(edges) + 1,
                        (*frame_names, name),
                        (*edge_indices, index),
                        counter,
                        next_frame,
                        (*edges, edge),
                        (*transforms, transform),
                        (*reversed_edges, reversed_edge),
                    ),
                )
        return None


def _validate_validity_region(
    region: ValidityRegion, target: CoordinateFrame
) -> ValidityRegion:
    if isinstance(region, Polygon):
        if target.ndim != 2:
            raise ValueError(
                'polygon validity regions require a 2-D target frame'
            )
        return region
    if isinstance(region, (str, bytes)):
        raise TypeError('validity bounds must be a (lower, upper) pair')
    try:
        bounds = tuple(region)
    except TypeError as exc:
        raise TypeError(
            'validity_region must be a Polygon, bounds pair, or None'
        ) from exc
    if len(bounds) != 2:
        raise ValueError(
            'validity bounds must contain lower and upper coordinates'
        )
    try:
        lower = tuple(bounds[0])
        upper = tuple(bounds[1])
    except TypeError as exc:
        raise TypeError(
            'validity bounds coordinates must be sequences'
        ) from exc
    if len(lower) != target.ndim or len(upper) != target.ndim:
        raise ValueError(
            'validity bounds must match the target frame dimension'
        )
    if any(
        isinstance(value, bool) or not isinstance(value, Real)
        for value in (*lower, *upper)
    ):
        raise TypeError('validity bounds must contain real numbers')
    normalized_lower = tuple(float(value) for value in lower)
    normalized_upper = tuple(float(value) for value in upper)
    if not all(
        math.isfinite(value)
        for value in (*normalized_lower, *normalized_upper)
    ):
        raise ValueError('validity bounds must be finite')
    if any(
        low > high
        for low, high in zip(normalized_lower, normalized_upper, strict=True)
    ):
        raise ValueError('validity lower bounds must not exceed upper bounds')
    return normalized_lower, normalized_upper


def _validate_resolution_frame(frame: Any, *, kind: str) -> None:
    if not isinstance(frame, CoordinateFrame):
        raise TypeError(f'{kind} must be a CoordinateFrame')


def _inverse_or_none(
    transform: CoordinateTransform,
) -> CoordinateTransform | None:
    try:
        inverse = transform.inverse()
    except NotImplementedError:
        return None
    if not isinstance(inverse, CoordinateTransform):
        raise TypeError('transform inverse must satisfy CoordinateTransform')
    if (
        inverse.source_frame is not transform.target_frame
        or inverse.target_frame is not transform.source_frame
    ):
        raise ValueError('transform inverse declares inconsistent frames')
    return inverse


def _orient_edge(
    edge: MappingEdge, source: CoordinateFrame, target: CoordinateFrame
) -> tuple[CoordinateTransform, bool] | None:
    if not isinstance(edge.transform, AffineMapping):
        return None
    if edge.source_frame is source and edge.target_frame is target:
        return edge.transform, False
    if edge.target_frame is source and edge.source_frame is target:
        inverse = _inverse_or_none(edge.transform)
        return None if inverse is None else (inverse, True)
    return None


def _orient_from(
    edge: MappingEdge, source: CoordinateFrame
) -> tuple[CoordinateFrame, CoordinateTransform, bool] | None:
    if not isinstance(edge.transform, AffineMapping):
        return None
    if edge.source_frame is source:
        return edge.target_frame, edge.transform, False
    if edge.target_frame is source:
        inverse = _inverse_or_none(edge.transform)
        if inverse is not None:
            return edge.source_frame, inverse, True
    return None


def _identity_path(frame: CoordinateFrame) -> ResolvedPath:
    mapping = AffineMapping(frame, frame, np.eye(frame.ndim))
    return ResolvedPath((), mapping, 'correction', 0.0, (), ())


def _resolved_path(
    source: CoordinateFrame,
    target: CoordinateFrame,
    edges: tuple[MappingEdge, ...],
    transforms: tuple[CoordinateTransform, ...],
    reversed_edges: tuple[bool, ...],
) -> ResolvedPath:
    mapping = _compose_affine_transforms(source, target, transforms)
    role = min((edge.role for edge in edges), key=_role_strength)
    accuracy = (
        None
        if any(edge.accuracy is None for edge in edges)
        else math.sqrt(sum(edge.accuracy**2 for edge in edges))
    )
    return ResolvedPath(
        edges,
        mapping,
        role,
        accuracy,
        tuple(edge.validity_region for edge in edges),
        reversed_edges,
    )


def _role_strength(role: MappingRole) -> int:
    return {'composition': 0, 'correspondence': 1, 'correction': 2}[role]


def _compose_affine_transforms(
    source: CoordinateFrame,
    target: CoordinateFrame,
    transforms: tuple[CoordinateTransform, ...],
) -> AffineMapping:
    matrix = np.eye(source.ndim)
    offset = np.zeros(source.ndim)
    current = source
    for transform in transforms:
        if not isinstance(transform, AffineMapping):
            raise NotImplementedError(
                'coordinate-graph resolution currently requires affine transforms'
            )
        assert transform.source_frame is current, (
            'coordinate-path invariant violated: transforms must be connected'
        )
        matrix = transform.matrix @ matrix
        offset = transform.matrix @ offset + transform.offset
        current = transform.target_frame
    assert current is target, (
        'coordinate-path invariant violated: path must end in the requested frame'
    )
    return AffineMapping(source, target, matrix, offset)
