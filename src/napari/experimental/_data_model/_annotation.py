from __future__ import annotations

import math
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from numbers import Real
from types import MappingProxyType
from typing import Any

import numpy as np

from napari.experimental._data_model._axis import CoordinateFrame
from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._geometry import PolygonSetDomain
from napari.experimental._data_model._validation import validate_name

_EMPTY_CONTEXT: Mapping[str, Any] = MappingProxyType({})


def _validate_real(value: Any, *, kind: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise TypeError(f'{kind} must be a real number')
    converted = float(value)
    if not np.isfinite(converted):
        raise ValueError(f'{kind} must be finite')
    return converted


def _validate_tolerance(tolerance: Any) -> float:
    converted = _validate_real(tolerance, kind='tolerance')
    if converted < 0:
        raise ValueError('tolerance must be non-negative')
    return converted


def _validate_context(context: Any) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise TypeError('context must be a mapping')
    copied = dict(context)
    for name in copied:
        validate_name(name, kind='context axis')
    return copied


def _context_values_equal(first: Any, second: Any) -> bool:
    if isinstance(first, (float, np.floating)) and isinstance(
        second, (float, np.floating)
    ):
        return math.isclose(first, second, rel_tol=1e-9)
    result = first == second
    if isinstance(result, np.ndarray):
        return bool(np.all(result))
    return bool(result)


@dataclass(frozen=True, slots=True, eq=False, init=False)
class PlaneAnchor:
    """Bind in-plane annotation geometry to a world-coordinate slice.

    A missing context axis means that the annotation applies at every value
    along that axis.
    """

    frame: CoordinateFrame
    plane_axis: str
    plane_value: float
    in_plane_axes: tuple[str, str]
    _context: dict[str, Any] = field(init=False, repr=False)

    def __init__(
        self,
        frame: CoordinateFrame,
        plane_axis: str,
        plane_value: float,
        in_plane_axes: tuple[str, str],
        context: Mapping[str, Any] = _EMPTY_CONTEXT,
    ) -> None:
        object.__setattr__(self, 'frame', frame)
        object.__setattr__(self, 'plane_axis', plane_axis)
        object.__setattr__(self, 'plane_value', plane_value)
        object.__setattr__(self, 'in_plane_axes', in_plane_axes)
        object.__setattr__(self, '_context', context)
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.frame, CoordinateFrame):
            raise TypeError('frame must be a CoordinateFrame')
        validate_name(self.plane_axis, kind='plane axis')
        frame_axis_names = tuple(axis.name for axis in self.frame.axes)
        if self.plane_axis not in frame_axis_names:
            raise ValueError('plane_axis must name an axis in frame')

        if isinstance(self.in_plane_axes, (str, bytes)):
            raise TypeError('in_plane_axes must be a pair of axis names')
        try:
            axes = tuple(self.in_plane_axes)
        except TypeError as exc:
            raise TypeError(
                'in_plane_axes must be an iterable pair of axis names'
            ) from exc
        if len(axes) != 2:
            raise ValueError('in_plane_axes must contain exactly two axes')
        for name in axes:
            validate_name(name, kind='in-plane axis')
            if name not in frame_axis_names:
                raise ValueError('in_plane_axes must name axes in frame')
        if len(set(axes)) != 2:
            raise ValueError('in_plane_axes must be distinct')
        if self.plane_axis in axes:
            raise ValueError('plane_axis and in_plane_axes must be disjoint')

        context = _validate_context(self._context)
        overlap = set(frame_axis_names).intersection(context)
        if overlap:
            raise ValueError(
                'context axes must not name axes of the anchor frame'
            )

        object.__setattr__(
            self,
            'plane_value',
            _validate_real(self.plane_value, kind='plane_value'),
        )
        object.__setattr__(self, 'in_plane_axes', axes)
        object.__setattr__(self, '_context', context)

    def __replace__(self, **changes: Any) -> PlaneAnchor:
        """Rebuild with replacements for ``copy.replace`` compatibility."""
        state: dict[str, Any] = {
            'frame': self.frame,
            'plane_axis': self.plane_axis,
            'plane_value': self.plane_value,
            'in_plane_axes': self.in_plane_axes,
            'context': dict(self._context),
        }
        state.update(changes)
        return PlaneAnchor(**state)

    replace = __replace__

    @property
    def context(self) -> Mapping[str, Any]:
        """Non-spatial selection context as a read-only mapping."""
        return MappingProxyType(self._context)


@dataclass(frozen=True, slots=True, eq=False)
class ROI:
    """Named polygon annotation attached to a target DataObject by identity."""

    name: str
    data: DataObject
    anchor: PlaneAnchor
    target: DataObject
    _target_domain: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        validate_name(self.name, kind='ROI')
        if not isinstance(self.data, DataObject):
            raise TypeError('ROI data must be a DataObject')
        if not isinstance(self.data.domain, PolygonSetDomain):
            raise TypeError('ROI data domain must be a PolygonSetDomain')
        if not isinstance(self.anchor, PlaneAnchor):
            raise TypeError('anchor must be a PlaneAnchor')
        if not isinstance(self.target, DataObject):
            raise TypeError('target must be a DataObject')

        object.__setattr__(self, '_target_domain', self.target.domain)
        domain_frame = self.data.domain.intrinsic_frame
        domain_axis_names = tuple(axis.name for axis in domain_frame.axes)
        if domain_axis_names != self.anchor.in_plane_axes:
            raise ValueError(
                'PolygonSetDomain axes must match anchor in_plane_axes in order'
            )
        anchor_axes = {axis.name: axis for axis in self.anchor.frame.axes}
        for domain_axis in domain_frame.axes:
            if domain_axis.unit != anchor_axes[domain_axis.name].unit:
                raise ValueError(
                    'PolygonSetDomain axis units must match anchor frame units'
                )

    @property
    def is_valid(self) -> bool:
        """Whether the target retains the domain on which this ROI was made.

        A replaced domain requires an explicitly recreated/revalidated anchor.
        Value or placement updates alone do not invalidate a world anchor.
        """
        return self.target.domain is self._target_domain

    def matches(
        self,
        plane_axis_value: float,
        context: Mapping[str, Any],
        *,
        tolerance: float,
        frame: CoordinateFrame | None = None,
    ) -> bool:
        """Return whether this ROI applies at a plane coordinate and context.

        Float context values use a relative tolerance of ``1e-9``; other
        values compare exactly. Every axis pinned by the anchor must be present
        in the query, so omitting a pinned axis does not match. When ``frame``
        is supplied, it must be the anchor's frame object by identity.
        Invalidated anchors also return False; inspect ``is_valid`` to
        distinguish them from valid anchors outside the queried plane.
        """
        plane_value = _validate_real(plane_axis_value, kind='plane_axis_value')
        absolute_tolerance = _validate_tolerance(tolerance)
        context_matches = self.matches_context(context, frame=frame)
        if abs(plane_value - self.anchor.plane_value) > absolute_tolerance:
            return False
        return context_matches

    def matches_context(
        self,
        context: Mapping[str, Any],
        *,
        frame: CoordinateFrame | None,
    ) -> bool:
        """Return whether this ROI applies in a frame and selection context.

        Float context values use a relative tolerance of ``1e-9``; other
        values compare exactly. Every axis pinned by the anchor must be
        present. ``frame`` must be the anchor's frame object by identity when
        it is not ``None``.
        """
        candidate_context = _validate_context(context)
        if not self.is_valid:
            return False
        if frame is not None and not isinstance(frame, CoordinateFrame):
            raise TypeError('frame must be a CoordinateFrame or None')
        if frame is not None and self.anchor.frame is not frame:
            return False
        return all(
            name in candidate_context
            and _context_values_equal(candidate_context[name], expected)
            for name, expected in self.anchor.context.items()
        )


class ROICollection:
    """Mutable ordered ROI attachment point for one target DataObject."""

    __slots__ = ('_rois', '_target', 'on_changed')

    def __init__(self, target: DataObject) -> None:
        if not isinstance(target, DataObject):
            raise TypeError('target must be a DataObject')
        self._target = target
        self._rois: list[ROI] = []
        self.on_changed: list[Callable[[], None]] = []

    @property
    def target(self) -> DataObject:
        """DataObject annotated by every ROI in this collection."""
        return self._target

    def __len__(self) -> int:
        return len(self._rois)

    def __iter__(self) -> Iterator[ROI]:
        return iter(self._rois)

    def add(self, roi: ROI) -> None:
        """Append an ROI and notify change callbacks."""
        if not isinstance(roi, ROI):
            raise TypeError('roi must be an ROI')
        if roi.target is not self._target:
            raise ValueError('ROI target must be this collection target')
        self._rois.append(roi)
        self._notify_changed()

    def remove(self, roi: ROI) -> None:
        """Remove an ROI by identity and notify change callbacks."""
        if not isinstance(roi, ROI):
            raise TypeError('roi must be an ROI')
        self._rois.remove(roi)
        self._notify_changed()

    def visible_at(
        self,
        plane_axis: str,
        plane_value: float,
        context: Mapping[str, Any],
        *,
        tolerance: float,
        frame: CoordinateFrame | None = None,
    ) -> tuple[ROI, ...]:
        """Return ROIs matching a frame, axis, coordinate, and context."""
        validate_name(plane_axis, kind='plane axis')
        checked_plane_value = _validate_real(plane_value, kind='plane_value')
        checked_context = _validate_context(context)
        checked_tolerance = _validate_tolerance(tolerance)
        if frame is not None and not isinstance(frame, CoordinateFrame):
            raise TypeError('frame must be a CoordinateFrame or None')
        return tuple(
            roi
            for roi in self._rois
            if roi.anchor.plane_axis == plane_axis
            and roi.matches(
                checked_plane_value,
                checked_context,
                tolerance=checked_tolerance,
                frame=frame,
            )
        )

    def _notify_changed(self) -> None:
        for callback in tuple(self.on_changed):
            callback()
