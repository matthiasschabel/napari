"""Polygon geometry backed by Shapely.

Napari core does not depend on Shapely. Requiring it here is an explicit
dependency decision for this experimental data-model branch.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    from shapely import contains_xy
    from shapely.geometry import (
        LineString as LineString,
        MultiPolygon as ShapelyMultiPolygon,
        Polygon as ShapelyPolygon,
    )
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union
    from shapely.validation import explain_validity
except ImportError as exc:  # pragma: no cover - exercised without test extras
    raise ImportError(
        'experimental polygon geometry requires Shapely; '
        "install it with 'pip install shapely'"
    ) from exc

from napari.experimental._data_model._axis import CoordinateFrame


def _copy_ring(ring: Any, *, kind: str) -> np.ndarray:
    try:
        copied = np.array(ring, dtype=float, copy=True)
    except (TypeError, ValueError) as exc:
        raise TypeError(f'{kind} ring must contain numeric values') from exc
    if copied.ndim != 2 or copied.shape[1:] != (2,):
        raise ValueError(f'{kind} ring must have shape (n, 2)')
    if len(copied) < 3:
        raise ValueError(f'{kind} ring must contain at least three vertices')
    if not np.all(np.isfinite(copied)):
        raise ValueError(f'{kind} ring vertices must be finite')
    if np.array_equal(copied[0], copied[-1]):
        raise ValueError(
            f'{kind} ring must be implicitly closed without a repeated '
            'last vertex'
        )
    if len(np.unique(copied, axis=0)) != len(copied):
        raise ValueError(f'{kind} ring vertices must be unique')
    return copied


def _ring_array(coordinates: Any) -> np.ndarray:
    ring = np.array(coordinates, dtype=float, copy=True)[:-1]
    ring.flags.writeable = False
    return ring


@dataclass(frozen=True, slots=True, eq=False, init=False)
class Polygon:
    """A valid Shapely polygon with normalized ring winding.

    Rings contain unique, implicitly closed vertices. Construction normalizes
    the exterior to counterclockwise winding and holes to clockwise winding.
    Containment follows Shapely's strict interior semantics, so points on
    either exterior or hole boundaries are excluded.

    Parameters
    ----------
    exterior : array-like, shape (n, 2)
        Exterior-ring vertices.
    holes : sequence of array-like
        Interior-ring vertices.
    """

    _geometry: ShapelyPolygon = field(repr=False)
    _exterior: np.ndarray = field(init=False, repr=False)
    _holes: tuple[np.ndarray, ...] = field(init=False, repr=False)

    def __init__(self, exterior: Any, holes: Any = ()) -> None:
        copied_exterior = _copy_ring(exterior, kind='exterior')
        if isinstance(holes, (str, bytes)):
            raise TypeError('holes must be an iterable of rings')
        try:
            hole_iterator = iter(holes)
        except TypeError as exc:
            raise TypeError('holes must be an iterable of rings') from exc
        copied_holes = tuple(
            _copy_ring(hole, kind='interior') for hole in hole_iterator
        )

        geometry = ShapelyPolygon(copied_exterior, copied_holes)
        if not geometry.is_valid:
            raise ValueError(
                f'invalid polygon geometry: {explain_validity(geometry)}'
            )
        normalized = orient(geometry, sign=1.0)
        object.__setattr__(self, '_geometry', normalized)
        object.__setattr__(
            self, '_exterior', _ring_array(normalized.exterior.coords)
        )
        object.__setattr__(
            self,
            '_holes',
            tuple(_ring_array(ring.coords) for ring in normalized.interiors),
        )

    @property
    def exterior(self) -> np.ndarray:
        """Read-only exterior-ring vertices without the closing duplicate."""
        return self._exterior

    @property
    def holes(self) -> tuple[np.ndarray, ...]:
        """Read-only interior-ring vertices without closing duplicates."""
        return self._holes

    def __repr__(self) -> str:
        vertex_count = len(self._exterior) + sum(map(len, self._holes))
        return (
            f'Polygon(n_holes={len(self._holes)}, n_vertices={vertex_count})'
        )

    def __replace__(self, **changes: Any) -> Polygon:
        """Rebuild with replacements for ``copy.replace`` compatibility."""
        state: dict[str, Any] = {
            'exterior': self.exterior,
            'holes': self.holes,
        }
        state.update(changes)
        return Polygon(**state)

    replace = __replace__

    def __deepcopy__(self, memo: dict[int, Any]) -> Polygon:
        copied = Polygon(self.exterior, self.holes)
        memo[id(self)] = copied
        return copied

    def __reduce__(
        self,
    ) -> tuple[Any, tuple[np.ndarray, tuple[np.ndarray, ...]]]:
        return (type(self), (self.exterior, self.holes))

    @property
    def bounding_box(self) -> np.ndarray:
        """Exterior bounds as ``[[min0, min1], [max0, max1]]``."""
        min_x, min_y, max_x, max_y = self._geometry.bounds
        return np.array([[min_x, min_y], [max_x, max_y]])

    @property
    def area(self) -> float:
        """Exterior area minus the areas of all holes."""
        return float(self._geometry.area)

    def contains_points(self, points: Any) -> np.ndarray:
        """Return whether each 2-D point lies strictly inside the polygon."""
        try:
            point_array = np.asarray(points, dtype=float)
        except (TypeError, ValueError) as exc:
            raise TypeError('points must contain numeric values') from exc
        if point_array.ndim != 2 or point_array.shape[1:] != (2,):
            raise ValueError('points must have shape (n, 2)')
        if not np.all(np.isfinite(point_array)):
            raise ValueError('points must be finite')
        return np.asarray(
            contains_xy(self._geometry, point_array[:, 0], point_array[:, 1]),
            dtype=bool,
        )


@dataclass(frozen=True, slots=True, eq=False, init=False)
class PolygonSetDomain:
    """Ordered polygon elements expressed in a two-axis coordinate frame.

    ``polygons`` preserves the input components and their order for
    per-polygon Fields. Display and measurement instead operate on the
    normalized union returned by :meth:`as_multipolygon`. A component inside
    another component's hole is therefore an island that contributes positive
    area, while the containing component's hole remains empty elsewhere.

    ``shape`` describes per-polygon Field storage and is therefore
    ``(n_elements,)``. The intrinsic frame instead describes the two
    coordinates used by every ring vertex; geometric coordinates and element
    storage are deliberately separate concepts.

    Parameters
    ----------
    polygons : iterable of Polygon
        Ordered input polygon elements. Their union is the geometry.
    axis_names : pair of str
        Names of the in-plane coordinate axes.
    axis_units : pair of str or None
        Units of the in-plane coordinate axes.
    """

    _polygons: tuple[Polygon, ...] = field(init=False, repr=False)
    axis_names: tuple[str, str]
    axis_units: tuple[str | None, str | None]
    _intrinsic_frame: CoordinateFrame = field(init=False, repr=False)
    _multipolygon: ShapelyMultiPolygon = field(init=False, repr=False)

    def __init__(
        self,
        polygons: Any,
        axis_names: tuple[str, str],
        axis_units: tuple[str | None, str | None] = (None, None),
    ) -> None:
        try:
            polygon_tuple = tuple(polygons)
        except TypeError as exc:
            raise TypeError(
                'polygons must be an iterable of Polygon objects'
            ) from exc
        if not all(isinstance(polygon, Polygon) for polygon in polygon_tuple):
            raise TypeError('polygons must contain only Polygon objects')

        if isinstance(axis_names, (str, bytes)):
            raise TypeError('axis_names must be a pair of names')
        if isinstance(axis_units, (str, bytes)):
            raise TypeError('axis_units must be a pair of units')
        try:
            names = tuple(axis_names)
            units = tuple(axis_units)
        except TypeError as exc:
            raise TypeError(
                'axis_names and axis_units must be iterable pairs'
            ) from exc
        if len(names) != 2:
            raise ValueError('axis_names must contain exactly two names')
        if len(units) != 2:
            raise ValueError('axis_units must contain exactly two units')
        intrinsic_frame = CoordinateFrame(
            'intrinsic', tuple(zip(names, units, strict=True))
        )

        normalized = unary_union(
            tuple(polygon._geometry for polygon in polygon_tuple)
        )
        if normalized.is_empty:
            multipolygon = ShapelyMultiPolygon()
        elif isinstance(normalized, ShapelyPolygon):
            multipolygon = ShapelyMultiPolygon((normalized,))
        elif isinstance(normalized, ShapelyMultiPolygon):
            multipolygon = normalized
        else:  # pragma: no cover - a union of valid polygons stays polygonal
            raise ValueError('polygon components must have polygonal union')
        if not multipolygon.is_valid:
            raise ValueError(
                'invalid polygon-set geometry: '
                f'{explain_validity(multipolygon)}'
            )

        object.__setattr__(self, '_polygons', polygon_tuple)
        object.__setattr__(self, 'axis_names', names)
        object.__setattr__(self, 'axis_units', units)
        object.__setattr__(self, '_intrinsic_frame', intrinsic_frame)
        object.__setattr__(self, '_multipolygon', multipolygon)

    @classmethod
    def from_multipolygon(
        cls,
        multipolygon: ShapelyMultiPolygon,
        axis_names: tuple[str, str],
        axis_units: tuple[str | None, str | None] = (None, None),
    ) -> PolygonSetDomain:
        """Record a Shapely MultiPolygon's components and normalize its union."""
        if not isinstance(multipolygon, ShapelyMultiPolygon):
            raise TypeError('multipolygon must be a shapely.MultiPolygon')
        polygons = tuple(
            Polygon(
                np.asarray(component.exterior.coords)[:-1],
                tuple(
                    np.asarray(interior.coords)[:-1]
                    for interior in component.interiors
                ),
            )
            for component in multipolygon.geoms
        )
        return cls(polygons, axis_names, axis_units)

    def as_multipolygon(self) -> ShapelyMultiPolygon:
        """Return the authoritative geometry: the normalized component union."""
        return self._multipolygon

    def __replace__(self, **changes: Any) -> PolygonSetDomain:
        """Rebuild with replacements for ``copy.replace`` compatibility."""
        state: dict[str, Any] = {
            'polygons': self._polygons,
            'axis_names': self.axis_names,
            'axis_units': self.axis_units,
        }
        state.update(changes)
        return PolygonSetDomain(**state)

    replace = __replace__

    def __deepcopy__(self, memo: dict[int, Any]) -> PolygonSetDomain:
        copied = PolygonSetDomain(
            deepcopy(self._polygons, memo), self.axis_names, self.axis_units
        )
        memo[id(self)] = copied
        return copied

    def __reduce__(
        self,
    ) -> tuple[
        Any,
        tuple[
            tuple[Polygon, ...],
            tuple[str, str],
            tuple[str | None, str | None],
        ],
    ]:
        return (type(self), (self._polygons, self.axis_names, self.axis_units))

    def __repr__(self) -> str:
        return (
            f'PolygonSetDomain(n_elements={self.n_elements}, '
            f'axis_names={self.axis_names!r}, axis_units={self.axis_units!r})'
        )

    @property
    def polygons(self) -> tuple[Polygon, ...]:
        """Ordered immutable input record used by per-polygon Fields."""
        return self._polygons

    @property
    def n_elements(self) -> int:
        """Number of polygon elements."""
        return len(self._polygons)

    @property
    def shape(self) -> tuple[int]:
        """Shape of per-element Fields."""
        return (self.n_elements,)

    @property
    def intrinsic_frame(self) -> CoordinateFrame:
        """Two-axis frame in which polygon vertices are expressed."""
        return self._intrinsic_frame
