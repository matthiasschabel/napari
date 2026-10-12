from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np

from napari.experimental._data_model._field_geometry import FieldGeometry
from napari.experimental._data_model._source import (
    DataSource,
    _validate_source,
    source_level_geometry,
)
from napari.experimental._data_model._validation import (
    validate_name,
    validate_unit,
)


class Interpolation(Enum):
    """Resampling policy carried by a field."""

    NEAREST = 'nearest'
    LINEAR = 'linear'


@dataclass(frozen=True, slots=True, eq=False)
class Field:
    """Values and value semantics defined over a domain.

    Parameters
    ----------
    name : str
        Field name.
    source : DataSource
        Region-readable storage.
    unit : str or None
        Pint-compatible value unit, independent of domain-axis units.
    interpolation : Interpolation or None
        Resampling policy. Omitted scalar policies retain legacy linear
        interpolation; categorical values use nearest. Declared component,
        vector and tensor values have no implicit resampling policy.
    geometry : FieldGeometry or None
        Shared value/basis/packing declaration. Component shape alone never
        declares a physical vector or tensor.
    missing_value : object or None
        Value used to represent missing samples.
    component_axes : tuple of int
        Storage dimensions containing value components. Remaining storage
        dimensions map to domain axes in order.
    dimensions : tuple of str or None
        Associated domain axes, in non-component storage order. None retains
        the legacy full-domain association. An empty tuple describes a value
        constant over the domain. Names and cardinalities are checked when
        attached to a DataObject through the domain association_shape method.
        Grid names address axes; mesh names address point or cell elements.
    """

    name: str
    source: DataSource
    unit: str | None = None
    interpolation: Interpolation | None = None
    missing_value: Any | None = None
    component_axes: tuple[int, ...] = ()
    dimensions: tuple[str, ...] | None = None
    geometry: FieldGeometry | None = None

    def __post_init__(self) -> None:
        validate_name(self.name, kind='field')
        validate_unit(self.unit, kind='field')
        if self.geometry is not None and not isinstance(
            self.geometry, FieldGeometry
        ):
            raise TypeError('geometry must be a FieldGeometry')
        if self.interpolation is not None and not isinstance(
            self.interpolation, Interpolation
        ):
            raise TypeError('interpolation must be an Interpolation or None')
        if self.interpolation is None:
            if self.geometry is None or self.geometry.kind in (
                'scalar',
                'complex',
            ):
                object.__setattr__(self, 'interpolation', Interpolation.LINEAR)
            elif self.geometry.kind == 'categorical':
                object.__setattr__(
                    self, 'interpolation', Interpolation.NEAREST
                )
        if (
            self.geometry is not None
            and self.geometry.kind == 'categorical'
            and self.interpolation is not Interpolation.NEAREST
        ):
            raise ValueError(
                'categorical values require nearest interpolation'
            )

        shape, _ = _validate_source(self.source)

        component_axes = tuple(self.component_axes)
        if any(
            isinstance(axis, bool) or not isinstance(axis, int)
            for axis in component_axes
        ):
            raise TypeError('component axes must be integers')
        if len(component_axes) != len(set(component_axes)):
            raise ValueError('component axes must be unique')
        if any(axis < 0 or axis >= len(shape) for axis in component_axes):
            raise ValueError('component axis is outside the source shape')
        object.__setattr__(self, 'component_axes', component_axes)
        if self.geometry is not None:
            self.geometry.validate_storage(
                tuple(shape[axis] for axis in component_axes), self.dtype
            )
        if component_axes:
            for level in range(self.source.levels):
                level_shape = self.source.level_shape(level)
                if tuple(
                    level_shape[axis] for axis in component_axes
                ) != tuple(shape[axis] for axis in component_axes):
                    raise ValueError(
                        'component cardinalities must be unchanged at every level'
                    )
                geometry = source_level_geometry(self.source, level)
                if geometry is not None and not geometry.is_identity(
                    component_axes
                ):
                    raise ValueError(
                        'component dimensions require identity level geometry'
                    )
        if self.dimensions is not None:
            if isinstance(self.dimensions, (str, bytes)):
                raise TypeError('field dimensions must be a sequence of names')
            dimensions = tuple(self.dimensions)
            for name in dimensions:
                validate_name(name, kind='field dimension')
            if len(dimensions) != len(set(dimensions)):
                raise ValueError('field dimensions must be unique')
            if len(dimensions) != len(self.domain_storage_axes):
                raise ValueError(
                    'field dimensions must match the non-component storage axes'
                )
            object.__setattr__(self, 'dimensions', dimensions)

    def require_full_grid(self, axis_names: tuple[str, ...]) -> None:
        """Reject associations that a full-grid consumer cannot interpret."""
        if self.dimensions is not None and self.dimensions != axis_names:
            raise NotImplementedError(
                'this consumer requires a full-grid field in domain axis order'
            )

    @property
    def shape(self) -> tuple[int, ...]:
        """Storage shape, including field component dimensions."""
        return tuple(int(size) for size in self.source.shape)

    @property
    def dtype(self) -> np.dtype[Any]:
        """Storage dtype."""
        return np.dtype(self.source.dtype)

    @property
    def domain_storage_axes(self) -> tuple[int, ...]:
        """Storage dimensions that map to domain axes in order."""
        components = set(self.component_axes)
        return tuple(
            axis for axis in range(len(self.shape)) if axis not in components
        )

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        """Read a storage region at one resolution level."""
        return self.source.read(region, level=level)
