from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np

from napari.experimental._data_model._axis import CoordinateAxis
from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._field import Field


class CoordinateSelection:
    """Coordinate restrictions expressed by axis name.

    Integer selectors are positional indices; negative indices are rejected.
    Non-integer selectors are coordinate values. Numeric and datetime values
    use ``method`` when no exact match exists.

    Parameters
    ----------
    selections : mapping of str to object
        Axis names mapped to indices or coordinate values.
    method : {"exact", "nearest"}
        Value-resolution method. Integer positional selectors are unaffected.
    """

    __slots__ = ('_method', '_selections')

    def __init__(
        self,
        selections: Mapping[str, Any],
        *,
        method: Literal['exact', 'nearest'] = 'nearest',
    ) -> None:
        if not isinstance(selections, Mapping):
            raise TypeError('selections must be a mapping')
        copied = dict(selections)
        if not all(isinstance(name, str) and name.strip() for name in copied):
            raise ValueError('selection axis names must be non-empty strings')
        if method not in {'exact', 'nearest'}:
            raise ValueError("selection method must be 'exact' or 'nearest'")
        self._selections = copied
        self._method = method

    @property
    def selections(self) -> dict[str, Any]:
        """Copy of the requested axis selections."""
        return dict(self._selections)

    @property
    def method(self) -> Literal['exact', 'nearest']:
        """Value-resolution method."""
        return self._method

    def apply(self, data_object: DataObject) -> dict[str, tuple[slice, ...]]:
        """Return field-specific source regions for a data object."""
        indices = self._resolve_indices(data_object)
        return {
            name: self._region_for_field(
                model_field,
                self._field_indices(data_object, model_field, indices),
            )
            for name, model_field in data_object.fields.items()
        }

    def region_for(
        self, data_object: DataObject, field_name: str
    ) -> tuple[slice, ...]:
        """Return the source region for one named field."""
        indices = self._resolve_indices(data_object)
        try:
            model_field = data_object.fields[field_name]
        except KeyError as exc:
            raise KeyError(f'unknown field {field_name!r}') from exc
        return self._region_for_field(
            model_field, self._field_indices(data_object, model_field, indices)
        )

    def read(
        self,
        data_object: DataObject,
        field_name: str,
        *,
        level: int = 0,
    ) -> np.ndarray:
        """Read a field region and remove the selected domain dimensions.

        Coordinate selections currently resolve against level-zero domain
        indices. Reading a differently shaped pyramid level therefore raises
        until a per-level index policy is defined.
        """
        indices = self._resolve_indices(data_object)
        try:
            model_field = data_object.fields[field_name]
        except KeyError as exc:
            raise KeyError(f'unknown field {field_name!r}') from exc
        if (
            level != 0
            and model_field.source.level_shape(level) != model_field.shape
        ):
            raise ValueError(
                f'coordinate selection at level {level} requires a '
                'per-level index policy when the level shape differs from '
                'level zero'
            )
        indices = self._field_indices(data_object, model_field, indices)
        region = self._region_for_field(model_field, indices)
        result = model_field.read(region, level=level)
        selected_storage_axes = tuple(
            model_field.domain_storage_axes[domain_axis]
            for domain_axis in sorted(indices)
        )
        if selected_storage_axes:
            result = np.squeeze(result, axis=selected_storage_axes)
        return result

    @staticmethod
    def _field_indices(
        data_object: DataObject, model_field: Field, indices: dict[int, int]
    ) -> dict[int, int]:
        if model_field.dimensions is None:
            return indices
        selected = {
            data_object.domain.axes[axis].name: index
            for axis, index in indices.items()
        }
        return {
            axis: selected[name]
            for axis, name in enumerate(model_field.dimensions)
            if name in selected
        }

    def _resolve_indices(self, data_object: DataObject) -> dict[int, int]:
        if not isinstance(data_object, DataObject):
            raise TypeError('selection requires a DataObject')
        domain = data_object.domain
        if self._selections and not hasattr(domain, 'axes'):
            raise TypeError('coordinate selection requires a domain with axes')
        axes = domain.axes if hasattr(domain, 'axes') else ()
        indices: dict[int, int] = {}
        for name, selector in self._selections.items():
            try:
                domain_index = next(
                    index
                    for index, axis in enumerate(axes)
                    if axis.name == name
                )
            except StopIteration as exc:
                raise KeyError(f'unknown selection axis {name!r}') from exc
            axis = axes[domain_index]
            # StructuredGridDomain.__post_init__ rejects COMPONENT axes.
            indices[domain_index] = self._resolve_axis_index(axis, selector)
        return indices

    def _resolve_axis_index(self, axis: CoordinateAxis, selector: Any) -> int:
        if isinstance(selector, (int, np.integer)) and not isinstance(
            selector, (bool, np.bool_)
        ):
            index = int(selector)
            if index < 0 or index >= axis.size:
                raise IndexError(
                    f'selection index {index} is outside axis {axis.name!r}'
                )
            return index

        if axis.categorical_labels is not None and isinstance(selector, str):
            try:
                return axis.categorical_labels.index(selector)
            except ValueError as exc:
                raise ValueError(
                    f'value {selector!r} is not present on axis {axis.name!r}'
                ) from exc

        if axis.values is None:
            raise ValueError(
                f'axis {axis.name!r} has no explicit values to resolve'
            )

        exact = np.flatnonzero(axis.values == selector)
        if exact.size:
            return int(exact[0])
        if axis.dtype_kind == 'categorical':
            raise ValueError(
                f'value {selector!r} is not present on axis {axis.name!r}'
            )
        if self.method == 'exact':
            raise ValueError(
                f'value {selector!r} is not present on axis {axis.name!r}'
            )
        try:
            distances = np.abs(axis.values - selector)
            return int(np.argmin(distances))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f'value {selector!r} cannot be resolved on axis {axis.name!r}'
            ) from exc

    @staticmethod
    def _region_for_field(
        model_field: Field, indices: dict[int, int]
    ) -> tuple[slice, ...]:
        region = [slice(None)] * len(model_field.shape)
        for domain_axis, index in indices.items():
            storage_axis = model_field.domain_storage_axes[domain_axis]
            region[storage_axis] = slice(index, index + 1)
        return tuple(region)
