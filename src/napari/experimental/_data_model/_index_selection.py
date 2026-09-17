"""Explicit grid selections that keep acquisition coordinates paired with values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from itertools import pairwise, product
from numbers import Integral
from typing import Any

import numpy as np

from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._domain import StructuredGridDomain
from napari.experimental._data_model._mapping import CoordinateEmbedding
from napari.experimental._data_model._source import (
    DataSource,
    SourceChangedError,
    _normalize_region,
    _validate_level,
    _validate_region,
    source_revision,
)

_Index = range | np.ndarray
_Dependency = tuple[DataSource, int]


class _IndexedSource:
    """Read only selected runs; never fetch the gaps between distant indices."""

    def __init__(
        self,
        source: DataSource,
        indices: tuple[_Index, ...],
        dependencies: tuple[_Dependency, ...],
    ) -> None:
        if source.levels != 1:
            raise NotImplementedError(
                'indexed views currently require a single-level source'
            )
        self.source = source
        self.indices = indices
        self.dependencies = dependencies
        self.shape = tuple(len(index) for index in indices)
        self.dtype = source.dtype
        self.levels = 1

    @property
    def revision(self) -> int:
        return source_revision(self.source) + sum(
            source_revision(source) for source, _ in self.dependencies
        )

    def _check_dependencies(self) -> None:
        if any(
            source_revision(source) != revision
            for source, revision in self.dependencies
        ):
            raise SourceChangedError(
                'selection coordinates changed; recreate the selection'
            )

    def level_shape(self, level: int) -> tuple[int, ...]:
        _validate_level(level, 1)
        return self.shape

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        _validate_level(level, 1)
        _validate_region(region, self.shape)
        normalized = _normalize_region(region, self.shape)
        self._check_dependencies()
        revision = self.revision
        selected = tuple(
            np.asarray(index[item], dtype=np.intp)
            for index, item in zip(self.indices, normalized, strict=True)
        )
        shape = tuple(len(index) for index in selected)
        if any(size == 0 for size in shape):
            return np.empty(shape, dtype=self.dtype)
        reversed_axes = tuple(
            axis
            for axis, index in enumerate(selected)
            if len(index) > 1 and np.all(np.diff(index) < 0)
        )
        selected = tuple(
            index[::-1] if axis in reversed_axes else index
            for axis, index in enumerate(selected)
        )
        runs = tuple(_read_runs(index) for index in selected)
        if all(len(axis_runs) == 1 for axis_runs in runs):
            result = self.source.read(
                tuple(axis_runs[0][1] for axis_runs in runs)
            )
        else:
            result = np.empty(shape, dtype=self.dtype)
            for combination in product(*runs):
                output = tuple(run[0] for run in combination)
                source_region = tuple(run[1] for run in combination)
                result[output] = self.source.read(source_region)
        if revision != self.revision:
            raise SourceChangedError('source changed during indexed read')
        return np.flip(result, axis=reversed_axes) if reversed_axes else result


def _read_runs(indices: np.ndarray) -> tuple[tuple[slice, slice], ...]:
    if len(indices) == 1:
        return ((slice(0, 1), slice(int(indices[0]), int(indices[0]) + 1)),)
    differences = np.diff(indices)
    if differences[0] > 0 and np.all(differences == differences[0]):
        return (
            (
                slice(0, len(indices)),
                slice(
                    int(indices[0]), int(indices[-1]) + 1, int(differences[0])
                ),
            ),
        )
    boundaries = (
        0,
        *(int(i) + 1 for i in np.flatnonzero(differences != 1)),
        len(indices),
    )
    return tuple(
        (
            slice(start, stop),
            slice(int(indices[start]), int(indices[stop - 1]) + 1),
        )
        for start, stop in pairwise(boundaries)
    )


def _indices(selector: Any, size: int) -> _Index:
    if isinstance(selector, slice):
        return range(*selector.indices(size))
    if isinstance(selector, Integral) and not isinstance(
        selector, (bool, np.bool_)
    ):
        index = int(selector)
        index = index + size if index < 0 else index
        if not 0 <= index < size:
            raise IndexError('selection index is outside the domain axis')
        return range(index, index + 1)
    values = np.asarray(selector)
    if values.ndim != 1 or (
        values.size and not np.issubdtype(values.dtype, np.integer)
    ):
        raise TypeError(
            'index selection requires an integer, slice, or integer vector'
        )
    if values.size and (
        np.any(values >= size)
        or (values.dtype.kind != 'u' and np.any(values < -size))
    ):
        raise IndexError('selection index is outside the domain axis')
    values = values.astype(np.intp, copy=True)
    values[values < 0] += size
    if np.any(values < 0) or np.any(values >= size):
        raise IndexError('selection index is outside the domain axis')
    values.flags.writeable = False
    return values


def isel(
    obj: DataObject,
    selections: Mapping[str, Any],
    *,
    dependencies: tuple[_Dependency, ...] = (),
) -> DataObject:
    """Build a lazy index selection, retaining selected dimensions.

    Integer vectors preserve order and duplicates. Scalar indices retain a
    length-one axis. Python slices and negative indices follow NumPy indexing.
    Non-affine gathers along embedded axes are explicitly unsupported.
    """
    if not isinstance(obj.domain, StructuredGridDomain):
        raise TypeError('indexed selection requires a StructuredGridDomain')
    if not isinstance(selections, Mapping):
        raise TypeError('index selections must be a mapping')
    snapshot = obj.replace()
    domain = snapshot.domain
    selected = {
        name: _indices(value, domain.axis(name).size)
        for name, value in selections.items()
    }
    axes = []
    for axis in domain.axes:
        if axis.name not in selected:
            axes.append(axis)
            continue
        indices = selected[axis.name]
        axes.append(
            axis.replace(
                size=len(indices),
                values=None
                if axis.values is None
                else axis.values[np.asarray(indices, dtype=np.intp)],
                categorical_labels=None
                if axis.categorical_labels is None
                else tuple(axis.categorical_labels[i] for i in indices),
                aux_values={
                    name: value[np.asarray(indices, dtype=np.intp)]
                    for name, value in axis.aux_values.items()
                },
            )
        )
    new_domain = StructuredGridDomain(tuple(axes))
    embeddings = []
    for embedding in snapshot.embeddings:
        matrix, offset = embedding.matrix.copy(), embedding.offset.copy()
        for i, axis in enumerate(domain.axes):
            if axis.name not in selected or not np.any(matrix[:, i]):
                continue
            indices = selected[axis.name]
            start = int(indices[0]) if len(indices) else 0
            step = (
                indices.step
                if isinstance(indices, range)
                else int(indices[1] - indices[0])
                if len(indices) > 1
                else 1
            )
            if (
                not isinstance(indices, range)
                and len(indices) > 2
                and np.any(np.diff(indices) != step)
            ):
                raise NotImplementedError(
                    'spatial index gather cannot be represented by an affine embedding'
                )
            offset += matrix[:, i] * start
            matrix[:, i] *= step
        embeddings.append(
            CoordinateEmbedding(
                new_domain, embedding.target_frame, matrix, offset
            )
        )

    def select_field(field):
        dimensions = field.dimensions
        if dimensions is None:
            dimensions = tuple(axis.name for axis in domain.axes)
        indices = [range(size) for size in field.shape]
        for name, storage_axis in zip(
            dimensions, field.domain_storage_axes, strict=True
        ):
            if name in selected:
                indices[storage_axis] = selected[name]
        source = _IndexedSource(field.source, tuple(indices), dependencies)
        return replace(field, source=source)

    return DataObject(
        snapshot.name,
        new_domain,
        {name: select_field(field) for name, field in snapshot.fields.items()},
        embeddings=tuple(embeddings),
        coordinates={
            name: select_field(field)
            for name, field in snapshot.coordinates.items()
        },
        metadata=snapshot.metadata,
    )


def sel(
    obj: DataObject,
    selections: Mapping[str, Any],
    *,
    duplicates: str = 'error',
) -> DataObject:
    """Select exact scalar coordinates, treating integers as values.

    A coordinate must be one-dimensional and scalar. Multiple constraints on
    one dimension intersect without sorting the observations. ``duplicates``
    is ``'error'`` or ``'all'``; there is no implicit nearest-neighbor choice.
    Lookup dependencies remain attached to the selected sources, so edits to
    the lookup coordinates require rebuilding the selection.
    """
    if not isinstance(selections, Mapping):
        raise TypeError('coordinate selections must be a mapping')
    if duplicates not in ('error', 'all'):
        raise ValueError("duplicates must be 'error' or 'all'")
    if not isinstance(obj.domain, StructuredGridDomain):
        raise TypeError('coordinate selection requires a StructuredGridDomain')
    revision = obj.revision
    snapshot = obj.replace()
    selected: dict[str, np.ndarray] = {}
    dependencies = []
    for name, value in selections.items():
        if np.ndim(value) != 0:
            raise TypeError('coordinate selection requires scalar values')
        missing = None
        if name in snapshot.coordinates:
            coordinate = snapshot.coordinates[name]
            if (
                coordinate.component_axes
                or coordinate.dimensions is None
                or len(coordinate.dimensions) != 1
            ):
                raise NotImplementedError(
                    'lookup requires a scalar one-dimensional coordinate'
                )
            axis_name = coordinate.dimensions[0]
            if (
                name in {axis.name for axis in snapshot.domain.axes}
                and axis_name != name
            ):
                raise ValueError(
                    'coordinate name is ambiguous with a domain axis'
                )
            dependency = (
                coordinate.source,
                source_revision(coordinate.source),
            )
            dependencies.append(dependency)
            values = coordinate.read((slice(None),))
            missing = coordinate.missing_value
        else:
            axis = snapshot.domain.axis(name)
            axis_name = name
            values = (
                axis.values
                if axis.values is not None
                else axis.categorical_labels
            )
            if values is None:
                raise ValueError(
                    'axis has no explicit coordinate values; use isel'
                )
            values = np.asarray(values)
        scalar = np.asarray(value)
        if (scalar.dtype.kind == 'b') != (values.dtype.kind == 'b'):
            raise TypeError(
                'boolean lookup values require a boolean coordinate'
            )
        if values.dtype.kind in 'iu' and scalar.dtype.kind in 'iuf':
            # Avoid NumPy promoting integer ticks to an inexact float comparison.
            if scalar.dtype.kind == 'f' and (
                not np.isfinite(value) or value != np.floor(value)
            ):
                raise ValueError(
                    f'no matching observations for coordinate {name!r}'
                )
            value = int(value)
            limits = np.iinfo(values.dtype)
            if not limits.min <= value <= limits.max:
                raise ValueError(
                    f'no matching observations for coordinate {name!r}'
                )
            value = np.asarray(value, dtype=values.dtype)
        elif values.dtype.kind == 'f' and scalar.dtype.kind in 'iu':
            converted = values.dtype.type(value)
            if not np.isfinite(converted) or int(converted) != int(value):
                raise ValueError(
                    f'no matching observations for coordinate {name!r}'
                )
            value = converted
        elif values.dtype.kind == 'f' and scalar.dtype.kind == 'f':
            values = values.astype(
                np.result_type(values.dtype, scalar.dtype), copy=False
            )
        matches_mask = values == value
        if missing is not None:
            if np.ndim(missing) != 0:
                raise ValueError(
                    'lookup coordinate missing value must be scalar'
                )
            matches_mask &= values != missing
        matches = np.flatnonzero(matches_mask)
        if axis_name in selected:
            matches = matches[np.isin(matches, selected[axis_name])]
        if not matches.size:
            raise ValueError(
                f'no matching observations for coordinate {name!r}'
            )
        selected[axis_name] = matches
    if duplicates == 'error' and any(
        len(indices) > 1 for indices in selected.values()
    ):
        raise ValueError(
            "coordinate matches multiple observations; use duplicates='all' or isel"
        )
    if revision != obj.revision or any(
        source_revision(source) != version for source, version in dependencies
    ):
        raise SourceChangedError(
            'coordinates changed while resolving selection'
        )
    return isel(snapshot, selected, dependencies=tuple(dependencies))
