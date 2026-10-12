"""Stage C display composition for compositional data objects.

Irregular physical axis values cannot yet drive napari sliders. Such axes stay
index-spaced in the layer transform, and their physical values are retained in
``napari:irregular_axes`` metadata for the Stage 4 slider integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._display_sampling import (
    _read_display_grid,
)
from napari.experimental._data_model._domain import StructuredGridDomain
from napari.experimental._data_model._field import Field, Interpolation
from napari.experimental._data_model._mapping import (
    CoordinateEmbedding,
    _embedding_coordinates,
)
from napari.experimental._data_model._source import (
    DataSource,
    SourceChangedError,
    source_level_chunks,
    source_level_geometry,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from napari.layers import Image, Labels

_IRREGULAR_AXES_METADATA_KEY = 'napari:irregular_axes'
_DATA_OBJECT_METADATA_KEY = 'napari:data_object'
_REGULAR_COORDINATE_RTOL = 1e-12
_REGULAR_COORDINATE_ATOL = 1e-12


class _SourceRegionView:
    """Compose slice-only indexing before materializing a source region."""

    __slots__ = ('_level', '_source', '_window')

    def __init__(
        self,
        source: DataSource,
        level: int,
        window: tuple[slice, ...],
    ) -> None:
        self._source = source
        self._level = level
        self._window = window

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(_slice_length(item) for item in self._window)

    @property
    def ndim(self) -> int:
        return len(self.shape)

    @property
    def size(self) -> int:
        return int(np.prod(self.shape))

    @property
    def dtype(self) -> np.dtype[Any]:
        return np.dtype(self._source.dtype)

    def __getitem__(self, key: Any) -> np.ndarray | _SourceRegionView:
        region, squeezed_axes = _index_to_region(key, self.shape)
        window = _compose_regions(self._window, region, self.shape)
        if not squeezed_axes:
            return _SourceRegionView(self._source, self._level, window)

        result = self._read_display_region(window)
        if squeezed_axes:
            result = np.squeeze(result, axis=squeezed_axes)
        return result

    def _read_display_region(self, region: tuple[slice, ...]) -> np.ndarray:
        # Retry the complete display read once. Scientific calculations own
        # their retry boundary and must not silently mix source revisions.
        for attempt in range(2):
            try:
                return _read_display_grid(self._source, region, self._level)
            except SourceChangedError:
                if attempt:
                    raise
        raise AssertionError('display read must return or raise')

    def __array__(
        self, dtype: Any = None, copy: bool | None = None
    ) -> np.ndarray:
        result = self._read_display_region(self._window)
        if copy is None:
            return np.asarray(result, dtype=dtype)
        return np.array(result, dtype=dtype, copy=copy)


class _SourceLevelArray(_SourceRegionView):
    """Expose one DataSource level through napari's indexing interface.

    Slice-only indexing returns lazy region views so napari can compose its
    display crop with point slicing before one source read. Full conversion via
    :func:`numpy.asarray` remains eager because napari uses it to materialize
    the coarsest thumbnail level.
    """

    __slots__ = ()

    def __init__(self, source: DataSource, level: int) -> None:
        shape = tuple(int(size) for size in source.level_shape(level))
        window = tuple(slice(0, size, 1) for size in shape)
        super().__init__(source, level, window)

    @property
    def chunks(self) -> tuple[int, ...] | None:
        # Only the full-level wrapper may advertise chunks: a cropped
        # region view's chunk grid would be offset by the crop origin, so
        # exposing the level chunks there would misalign consumers.
        return source_level_chunks(self._source, self._level)


class _SourceArray(_SourceLevelArray):
    """Expose a DataSource's finest level through napari indexing."""

    __slots__ = ()

    def __init__(self, source: DataSource) -> None:
        super().__init__(source, 0)


def _slice_length(item: slice) -> int:
    return len(range(item.start, item.stop, item.step))


def _compose_regions(
    parent: tuple[slice, ...],
    child: tuple[slice, ...],
    child_shape: tuple[int, ...],
) -> tuple[slice, ...]:
    composed: list[slice] = []
    for parent_item, child_item, size in zip(
        parent, child, child_shape, strict=True
    ):
        try:
            start, stop, step = child_item.indices(size)
        except TypeError as exc:
            raise TypeError(
                'source slice bounds and steps must be integers or None'
            ) from exc
        if step < 0:
            raise ValueError('source slices do not support negative steps')

        parent_start = parent_item.start
        parent_stop = parent_item.stop
        parent_step = parent_item.step
        composed.append(
            slice(
                min(parent_start + start * parent_step, parent_stop),
                min(parent_start + stop * parent_step, parent_stop),
                parent_step * step,
            )
        )
    return tuple(composed)


def _index_to_region(
    key: Any, shape: tuple[int, ...]
) -> tuple[tuple[slice, ...], tuple[int, ...]]:
    """Normalize napari indexing into a source region plus squeezed axes."""
    ndim = len(shape)
    items = list(key if isinstance(key, tuple) else (key,))
    if items.count(Ellipsis) > 1:
        raise IndexError('an index can only have a single ellipsis')
    if Ellipsis in items:
        position = items.index(Ellipsis)
        missing = ndim - (len(items) - 1)
        items[position : position + 1] = [slice(None)] * missing
    if len(items) > ndim:
        raise IndexError('too many indices for source')
    items.extend([slice(None)] * (ndim - len(items)))

    region: list[slice] = []
    squeezed_axes: list[int] = []
    for axis, item in enumerate(items):
        if isinstance(item, int | np.integer):
            index = int(item)
            if index < 0:
                index += shape[axis]
            if index < 0 or index >= shape[axis]:
                raise IndexError('source index is out of bounds')
            region.append(slice(index, index + 1))
            squeezed_axes.append(axis)
        elif isinstance(item, slice):
            region.append(item)
        else:
            raise TypeError('source indices must be integers or slices')

    return tuple(region), tuple(squeezed_axes)


def add_to_viewer(
    viewer: Any,
    obj: DataObject,
    field: str = 'magnitude',
    *,
    source: DataSource | None = None,
) -> Image | Labels:
    """Add one DataObject field to a viewer as a raster layer.

    This Stage C bridge accepts any viewer-like object exposing ``add_layer``
    and ``layers``. Regular non-spatial axis values become scale and translate;
    irregular values remain index-spaced and are copied to
    ``napari:irregular_axes`` metadata because napari sliders cannot represent
    irregular physical coordinates until Stage 4.

    Spatial placement supports one coordinate embedding whose every target row
    maps to exactly one distinct domain axis. Coupled affine rows are deferred.
    If a spatial axis has both explicit values and an embedding row, the
    embedding takes precedence because the intrinsic frame is index space and
    the embedding is authoritative for placement.

    Slider labels remain the domain axis names. For a permuted embedding, each
    domain label therefore carries the scale, translation, and unit from the
    target-frame row mapped to that domain axis.

    The field's :class:`Interpolation` policy chooses the layer's screen
    resampling: a continuous field is displayed with ``interpolation2d``
    ``'linear'``, a categorical one with ``'nearest'``. Labels layers are
    nearest by construction.

    ``source`` overrides only the array exposed to the layer. Its geometry and
    dtype must match the field source, allowing a view-specific wrapper such as
    :class:`ProgressiveSource` without changing the scientific field.
    """
    from napari.layers import Image, Labels

    if not isinstance(obj, DataObject):
        raise TypeError('obj must be a DataObject')
    if not isinstance(obj.domain, StructuredGridDomain):
        raise TypeError('add_to_viewer requires a StructuredGridDomain')
    if not hasattr(viewer, 'add_layer') or not hasattr(viewer, 'layers'):
        raise TypeError('viewer must expose add_layer and layers')
    model_field = _get_view_field(obj, field)
    view_source = model_field.source if source is None else source
    if not isinstance(view_source, DataSource):
        raise TypeError('source must satisfy DataSource or be None')
    if (
        view_source.shape != model_field.source.shape
        or view_source.dtype != model_field.source.dtype
        or view_source.levels != model_field.source.levels
    ):
        raise ValueError(
            'source geometry and dtype must match the field source'
        )

    for level in range(view_source.levels):
        expected = source_level_geometry(model_field.source, level)
        actual = source_level_geometry(view_source, level)
        if (expected is None) != (actual is None) or (
            expected is not None and not expected.matches(actual)
        ):
            raise ValueError(
                'source level geometry must match the field source'
            )
    scale, translate, units, irregular_axes = _layer_coordinates(obj)
    metadata: dict[str, Any] = {_DATA_OBJECT_METADATA_KEY: obj}
    if irregular_axes:
        metadata[_IRREGULAR_AXES_METADATA_KEY] = irregular_axes
    common = {
        'axis_labels': tuple(axis.name for axis in obj.domain.axes),
        'metadata': metadata,
        'name': f'{obj.name}:{field}',
        'scale': scale,
        'translate': translate,
        'units': units,
    }
    multiscale = view_source.levels > 1
    data: _SourceArray | list[_SourceLevelArray]
    if multiscale:
        data = [
            _SourceLevelArray(view_source, level)
            for level in range(view_source.levels)
        ]
        common['multiscale'] = True
    else:
        data = _SourceArray(view_source)
    if _is_labels_field(model_field):
        layer: Image | Labels = Labels(data, **common)
        layer.editable = False
    else:
        layer = Image(
            data,
            rgb=False,
            interpolation2d=_layer_interpolation(model_field),
            **common,
        )
    viewer.add_layer(layer)
    return layer


def _get_view_field(obj: DataObject, field: str) -> Field:
    """Return a field supported by napari's raster layers."""
    if not isinstance(obj, DataObject):
        raise TypeError('obj must be a DataObject')
    try:
        model_field = obj.fields[field]
    except KeyError as exc:
        raise KeyError(f'unknown field {field!r}') from exc
    model_field.require_full_grid(tuple(axis.name for axis in obj.domain.axes))
    if model_field.component_axes:
        raise NotImplementedError(
            'raster display requires an explicitly selected scalar component'
        )
    if np.issubdtype(model_field.dtype, np.complexfloating):
        raise NotImplementedError(
            'napari Image layers do not support complex data; view complex '
            'fields through derived magnitude or phase fields'
        )
    return model_field


def _layer_interpolation(model_field: Field) -> str:
    """Screen resampling policy for one field's values.

    A continuous field is resampled linearly, so a coarse stand-in shown
    while its level is fetched reads as a soft image rather than a
    blocky one. A categorical field stays nearest: no value between two
    labels exists.
    """
    if model_field.interpolation is None:
        raise ValueError(
            'display resampling requires an explicit field interpolation policy'
        )
    return (
        'nearest'
        if model_field.interpolation is Interpolation.NEAREST
        else 'linear'
    )


def _is_labels_field(model_field: Field) -> bool:
    return (
        model_field.interpolation is Interpolation.NEAREST
        and np.issubdtype(model_field.dtype, np.integer)
    )


def _layer_coordinates(
    obj: DataObject,
) -> tuple[
    np.ndarray,
    np.ndarray,
    tuple[str | None, ...],
    Mapping[str, np.ndarray],
]:
    """Return layer placement, with embeddings overriding spatial values.

    The domain intrinsic frame is index space, so an embedding row is
    authoritative when its spatial axis also carries explicit values.
    """
    ndim = len(obj.domain.axes)
    scale = np.ones(ndim, dtype=float)
    translate = np.zeros(ndim, dtype=float)
    units: list[str | None] = [axis.unit for axis in obj.domain.axes]
    irregular_axes: dict[str, np.ndarray] = {}

    spatial_axes: set[int] = set()
    if len(obj.embeddings) > 1:
        raise NotImplementedError(
            'multiple coordinate embeddings are not supported by the '
            'Stage C view bridge'
        )
    if obj.embeddings:
        spatial_axes = _apply_embedding(
            obj.embeddings[0], scale, translate, units
        )

    for index, axis in enumerate(obj.domain.axes):
        if index in spatial_axes or axis.values is None or axis.size == 0:
            continue
        values = axis.values
        if not np.issubdtype(values.dtype, np.number):
            irregular_axes[axis.name] = values
            continue
        numeric_values = values.astype(float, copy=False)
        if axis.size == 1:
            translate[index] = numeric_values[0]
            continue
        differences = np.diff(numeric_values)
        if np.allclose(
            differences,
            differences[0],
            rtol=_REGULAR_COORDINATE_RTOL,
            atol=_REGULAR_COORDINATE_ATOL,
        ) and not np.isclose(
            differences[0],
            0.0,
            rtol=_REGULAR_COORDINATE_RTOL,
            atol=_REGULAR_COORDINATE_ATOL,
        ):
            scale[index] = differences[0]
            translate[index] = numeric_values[0]
        else:
            irregular_axes[axis.name] = values

    return scale, translate, tuple(units), irregular_axes


def _apply_embedding(
    embedding: CoordinateEmbedding,
    scale: np.ndarray,
    translate: np.ndarray,
    units: list[str | None],
) -> set[int]:
    domain_axes, spatial_scale, spatial_translate, spatial_units = (
        _embedding_coordinates(embedding)
    )
    for domain_axis, axis_scale, axis_translate, axis_unit in zip(
        domain_axes,
        spatial_scale,
        spatial_translate,
        spatial_units,
        strict=True,
    ):
        scale[domain_axis] = axis_scale
        translate[domain_axis] = axis_translate
        units[domain_axis] = axis_unit
    return set(domain_axes)
