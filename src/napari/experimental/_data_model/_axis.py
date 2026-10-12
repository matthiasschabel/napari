from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal

import numpy as np

from napari.experimental._data_model._validation import (
    validate_name,
    validate_unit,
)

AxisDtype = Literal['float', 'int', 'datetime', 'categorical']
_EMPTY_AUX_VALUES: Mapping[str, np.ndarray] = MappingProxyType({})


class AxisRole(Enum):
    """Classification of a storage axis in the compositional model."""

    COORDINATE = 'coordinate'
    COMPONENT = 'component'
    LOCAL = 'local'


@dataclass(frozen=True, slots=True, eq=False, init=False)
class CoordinateAxis:
    """Describe one intrinsic axis of a structured domain.

    Parameters
    ----------
    name : str
        Stable axis name.
    size : int
        Number of samples along the axis.
    role : AxisRole
        Axis classification. Structured domains accept coordinate and local
        axes; component descriptors are reserved for field storage.
    unit : str or None
        Pint-compatible unit spelling.
    values : numpy.ndarray or None
        Explicit one-dimensional coordinate values. Values need not be
        regularly spaced.
    dtype_kind : {"float", "int", "datetime", "categorical"}
        Semantic type of the coordinate values.
    categorical_labels : tuple of str or None
        Labels corresponding to categorical positions.
    aux_values : mapping of str to numpy.ndarray
        Additional coordinate variables. Their first dimension corresponds to
        this axis. The stored mapping and arrays are read-only.

    Notes
    -----
    Copy, deepcopy, pickle and dataclass replacement preserve auxiliary values.
    ``dataclasses.asdict`` and ``astuple`` cannot convert the read-only mapping;
    use pickle for object serialization or extract the public attributes.
    """

    name: str
    size: int
    role: AxisRole = AxisRole.COORDINATE
    unit: str | None = None
    values: np.ndarray | None = None
    dtype_kind: AxisDtype = 'float'
    categorical_labels: tuple[str, ...] | None = None
    aux_values: Mapping[str, np.ndarray] = field(default_factory=dict)

    def __init__(
        self,
        name: str,
        size: int,
        role: AxisRole = AxisRole.COORDINATE,
        unit: str | None = None,
        values: np.ndarray | None = None,
        dtype_kind: AxisDtype = 'float',
        categorical_labels: tuple[str, ...] | None = None,
        aux_values: Mapping[str, np.ndarray] = _EMPTY_AUX_VALUES,
    ) -> None:
        object.__setattr__(self, 'name', name)
        object.__setattr__(self, 'size', size)
        object.__setattr__(self, 'role', role)
        object.__setattr__(self, 'unit', unit)
        object.__setattr__(self, 'values', values)
        object.__setattr__(self, 'dtype_kind', dtype_kind)
        object.__setattr__(self, 'categorical_labels', categorical_labels)
        object.__setattr__(self, 'aux_values', aux_values)
        self.__post_init__()

    def __replace__(self, **changes: Any) -> CoordinateAxis:
        """Rebuild with replacements, preserving auxiliary values.

        Serves ``copy.replace`` (3.13+) and the ``replace`` method.
        ``dataclasses.replace`` also preserves auxiliary values.
        """
        state: dict[str, Any] = {
            'name': self.name,
            'size': self.size,
            'role': self.role,
            'unit': self.unit,
            'values': self.values,
            'dtype_kind': self.dtype_kind,
            'categorical_labels': self.categorical_labels,
            'aux_values': dict(self.aux_values),
        }
        state.update(changes)
        return CoordinateAxis(**state)

    replace = __replace__

    def __repr__(self) -> str:
        return (
            f'CoordinateAxis(name={self.name!r}, size={self.size}, '
            f'role={self.role}, unit={self.unit!r}, '
            f'has_values={self.values is not None}, '
            f'dtype_kind={self.dtype_kind!r}, '
            f'aux_values={list(self.aux_values)!r})'
        )

    def __post_init__(self) -> None:
        validate_name(self.name, kind='axis')
        if isinstance(self.size, bool) or not isinstance(self.size, int):
            raise TypeError('axis size must be an integer')
        if self.size < 0:
            raise ValueError('axis size must be non-negative')
        if not isinstance(self.role, AxisRole):
            raise TypeError('axis role must be an AxisRole')
        validate_unit(self.unit, kind='axis')
        if self.dtype_kind not in {
            'float',
            'int',
            'datetime',
            'categorical',
        }:
            raise ValueError(
                f'unsupported axis dtype kind {self.dtype_kind!r}'
            )

        if self.values is not None:
            self._validate_values(self.values)
            values = self.values.copy()
            values.flags.writeable = False
            object.__setattr__(self, 'values', values)

        if self.categorical_labels is not None:
            labels = tuple(self.categorical_labels)
            if self.dtype_kind != 'categorical':
                raise ValueError(
                    'categorical labels require dtype_kind="categorical"'
                )
            if len(labels) != self.size:
                raise ValueError(
                    'categorical label count must match the axis size'
                )
            if not all(isinstance(label, str) for label in labels):
                raise TypeError('categorical labels must be strings')
            object.__setattr__(self, 'categorical_labels', labels)

        if not isinstance(self.aux_values, Mapping):
            raise TypeError('aux_values must be a mapping')
        aux_values: dict[str, np.ndarray] = {}
        for name, values in self.aux_values.items():
            validate_name(name, kind='auxiliary coordinate')
            if not isinstance(values, np.ndarray):
                raise TypeError('auxiliary coordinate values must be arrays')
            if values.ndim == 0:
                raise ValueError(
                    'auxiliary coordinate values must have at least one '
                    'dimension'
                )
            if values.shape[0] != self.size:
                raise ValueError(
                    'auxiliary coordinate length must match the axis size'
                )
            copied_values = values.copy()
            copied_values.flags.writeable = False
            aux_values[name] = copied_values
        object.__setattr__(self, 'aux_values', MappingProxyType(aux_values))

    def __reduce__(self) -> tuple:
        return (
            type(self),
            (
                self.name,
                self.size,
                self.role,
                self.unit,
                self.values,
                self.dtype_kind,
                self.categorical_labels,
                dict(self.aux_values),
            ),
        )

    def _validate_values(self, values: np.ndarray) -> None:
        if not isinstance(values, np.ndarray):
            raise TypeError('axis values must be a numpy array')
        if values.ndim != 1:
            raise ValueError('axis values must be one-dimensional')
        if len(values) != self.size:
            raise ValueError('axis values length must match the axis size')

        valid_kinds = {
            'float': {'f'},
            'int': {'i', 'u'},
            'datetime': {'M'},
        }
        expected = valid_kinds.get(self.dtype_kind)
        if expected is not None and values.dtype.kind not in expected:
            raise TypeError(
                f'axis values dtype does not match {self.dtype_kind!r}'
            )


@dataclass(frozen=True, slots=True)
class FrameAxis:
    """Name and unit of one coordinate-frame axis."""

    name: str
    unit: str | None = None

    def __post_init__(self) -> None:
        validate_name(self.name, kind='frame axis')
        validate_unit(self.unit, kind='frame axis')


@dataclass(frozen=True, slots=True, eq=False)
class CoordinateFrame:
    """A named coordinate system whose equality is object identity.

    Parameters
    ----------
    name : str
        Stable frame name.
    axes : tuple of FrameAxis or tuple of (str, str or None)
        Ordered frame-axis descriptors.
    """

    name: str
    axes: tuple[FrameAxis | tuple[str, str | None], ...]

    def __post_init__(self) -> None:
        validate_name(self.name, kind='frame')
        axes = tuple(
            axis if isinstance(axis, FrameAxis) else FrameAxis(*axis)
            for axis in self.axes
        )
        names = [axis.name for axis in axes]
        if len(names) != len(set(names)):
            raise ValueError('frame axis names must be unique')
        object.__setattr__(self, 'axes', axes)

    @property
    def ndim(self) -> int:
        """Number of coordinates in this frame."""
        return len(self.axes)
