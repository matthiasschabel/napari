"""Explicit passive changes of basis for declared physical field values."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import numpy as np

from napari.experimental._data_model._axis import CoordinateFrame
from napari.experimental._data_model._field import Field
from napari.experimental._data_model._source import (
    SourceChangedError,
    _normalize_region,
    _validate_level,
    _validate_region,
    source_level_geometry,
    source_revision,
)

if TYPE_CHECKING:
    from napari.experimental._data_model._level_geometry import LevelGeometry

_ORTHOGONAL_ATOL = 1e-7
_ORTHOGONAL_RTOL = 1e-7


class _BasisSource:
    def __init__(self, field: Field, matrix: np.ndarray) -> None:
        self.field = field
        self.matrix = matrix
        self.shape = field.shape
        self.dtype = np.result_type(field.dtype, matrix.dtype)
        self.levels = field.source.levels

    @property
    def revision(self) -> int:
        return source_revision(self.field.source)

    def level_geometry(self, level: int) -> LevelGeometry | None:
        return source_level_geometry(self.field.source, level)

    def level_shape(self, level: int) -> tuple[int, ...]:
        return self.field.source.level_shape(level)

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        _validate_level(level, self.levels)
        shape = self.level_shape(level)
        _validate_region(region, shape)
        normalized = _normalize_region(region, shape)
        revision = self.revision
        axes = self.field.component_axes
        read_region = tuple(
            slice(None) if i in axes else item
            for i, item in enumerate(normalized)
        )
        values = self.field.read(read_region, level=level)
        values = np.moveaxis(values, axes, tuple(range(-len(axes), 0)))
        geometry = self.field.geometry
        missing = self.field.missing_value
        invalid = (
            None
            if missing is None
            else np.any(values == missing, axis=tuple(range(-len(axes), 0)))
        )
        if geometry.kind == 'vector':
            result = np.einsum('ij,...j->...i', self.matrix, values)
        else:
            if geometry.packing is not None:
                n = len(geometry.components)
                tensor = np.empty(
                    (*values.shape[:-1], n, n), dtype=values.dtype
                )
                for k, (i, j) in enumerate(geometry.packing):
                    tensor[..., i, j] = tensor[..., j, i] = values[..., k]
            else:
                tensor = values
            result = np.einsum(
                'ij,...jk,lk->...il', self.matrix, tensor, self.matrix
            )
            if geometry.packing is not None:
                result = np.stack(
                    [result[..., i, j] for i, j in geometry.packing], axis=-1
                )
        if invalid is not None:
            result[invalid] = missing
        result = np.moveaxis(result, tuple(range(-len(axes), 0)), axes)
        result = result[
            tuple(
                item if i in axes else slice(None)
                for i, item in enumerate(normalized)
            )
        ]
        if revision != self.revision:
            raise SourceChangedError('source changed during basis conversion')
        return result


def convert_field_basis(
    field: Field, target_frame: CoordinateFrame, matrix: np.ndarray
) -> Field:
    """Lazily express a vector or symmetric tensor in another orthonormal basis.

    ``matrix`` maps the field's declared component order to target-frame axis
    order. It must be orthogonal; reflections are allowed. Translation and
    voxel spacing are not inputs to this operation. Values retain their units,
    association and storage packing; computation promotes storage to at least
    float64. A requested component reads the full value
    at the requested samples before mixing components, never the full volume.
    This is passive basis conversion, not tensor reorientation under deformation.
    """
    if not isinstance(field, Field):
        raise TypeError('basis conversion requires a Field')
    if not isinstance(target_frame, CoordinateFrame):
        raise TypeError('target_frame must be a CoordinateFrame')
    geometry = field.geometry
    if (
        geometry is None
        or geometry.kind not in ('vector', 'symmetric_tensor')
        or geometry.basis != 'orthonormal'
    ):
        raise ValueError(
            'basis conversion requires an explicitly orthonormal physical value'
        )
    n = len(geometry.components)
    raw_matrix = np.asarray(matrix)
    if raw_matrix.dtype.kind not in 'fiu':
        raise TypeError('basis matrix must contain real numeric values')
    matrix = np.array(raw_matrix, dtype=float, copy=True)
    if matrix.shape != (n, n) or target_frame.ndim != n:
        raise ValueError(
            'basis matrix and target frame must match the component count'
        )
    # Accept direction cosines stored at float32 precision.
    if not np.all(np.isfinite(matrix)) or not np.allclose(
        matrix.T @ matrix,
        np.eye(n),
        atol=_ORTHOGONAL_ATOL,
        rtol=_ORTHOGONAL_RTOL,
    ):
        raise ValueError(
            'basis matrix must be orthogonal; scale and shear are unsupported'
        )
    from pint import get_application_registry

    registry = get_application_registry()
    units = [
        axis.unit
        for frame in (geometry.basis_frame, target_frame)
        for axis in frame.axes
    ]
    dimensions = [registry.Unit(unit or '').dimensionality for unit in units]
    if any(dimension != dimensions[0] for dimension in dimensions[1:]):
        raise ValueError('basis frames must have compatible axis units')
    if field.missing_value is not None:
        if np.ndim(field.missing_value) != 0:
            raise ValueError(
                'basis conversion requires a scalar missing value'
            )
        if np.asarray(field.missing_value).dtype.kind not in 'fiu':
            raise TypeError(
                'physical missing values must be real numeric scalars'
            )
    matrix.flags.writeable = False
    target_geometry = replace(
        geometry,
        basis_frame=target_frame,
        components=tuple(axis.name for axis in target_frame.axes),
    )
    return replace(
        field, source=_BasisSource(field, matrix), geometry=target_geometry
    )
