"""Strict adapters between legacy raster layers and compositional objects.

The intrinsic frame of a structured domain is index space. Axis ``values`` are
coordinate annotations on that frame, not an implicit coordinate embedding.
Until the Stage 2 coordinate graph can represent both, a markerless object with
numeric axis values and a world embedding is intentionally ambiguous.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import numpy as np

from napari.experimental._data_model._axis import (
    CoordinateAxis,
    CoordinateFrame,
)
from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._domain import StructuredGridDomain
from napari.experimental._data_model._field import Field, Interpolation
from napari.experimental._data_model._mapping import CoordinateEmbedding
from napari.experimental._data_model._source import ArraySource

if TYPE_CHECKING:
    from napari.layers import Image, Labels

_FIELD_NAME = 'data'
_LAYER_METADATA_KEY = 'napari:layer'
_REGULAR_COORDINATE_RTOL = 1e-12
_REGULAR_COORDINATE_ATOL = 1e-12


def data_object_from_layer(layer: Image | Labels) -> DataObject:
    """Capture an Image or Labels layer as a compositional data object.

    The adapter reads only array metadata and the layer's transform models. It
    does not index or materialize the layer data.
    """
    from napari.layers import Image, Labels

    if isinstance(layer, Labels):
        kind = 'labels'
        rgb = False
        interpolation = Interpolation.NEAREST
    elif isinstance(layer, Image):
        kind = 'image'
        rgb = bool(layer.rgb)
        interpolation = Interpolation.LINEAR
    else:
        raise TypeError('layer must be a napari Image or Labels layer')
    if layer.multiscale:
        raise NotImplementedError(
            'multiscale Image and Labels layers are not supported'
        )

    axis_labels = tuple(layer.axis_labels)
    axis_names = _axis_names(axis_labels, layer.ndim)
    axis_units = tuple(str(unit) for unit in layer.units)
    axes = tuple(
        CoordinateAxis(name, size, unit=unit)
        for name, size, unit in zip(
            axis_names, layer.data.shape[: layer.ndim], axis_units, strict=True
        )
    )
    domain = StructuredGridDomain(axes)
    component_axes = (layer.ndim,) if rgb else ()
    model_field = Field(
        _FIELD_NAME,
        ArraySource(layer.data),
        interpolation=interpolation,
        component_axes=component_axes,
    )

    world = CoordinateFrame(
        'world', tuple(zip(axis_names, axis_units, strict=True))
    )
    data_to_world = layer._data_to_world
    embedding = CoordinateEmbedding(
        domain,
        world,
        data_to_world.linear_matrix,
        data_to_world.translate,
    )
    metadata = dict(layer.metadata)
    marker: dict[str, Any] = {
        'embedding': embedding,
        'kind': kind,
        'rgb': rgb,
        'axis_labels': axis_labels,
        'transform': {
            'scale': np.array(layer.scale, copy=True),
            'translate': np.array(layer.translate, copy=True),
            'rotate': np.array(layer.rotate, copy=True),
            'shear': np.array(layer.shear, copy=True),
            'affine': np.array(layer.affine.affine_matrix, copy=True),
        },
        'metadata_key_present': _LAYER_METADATA_KEY in metadata,
    }
    if kind == 'image':
        marker['contrast_limits'] = tuple(layer.contrast_limits)
    if _LAYER_METADATA_KEY in metadata:
        marker['metadata_key_value'] = metadata[_LAYER_METADATA_KEY]
    metadata[_LAYER_METADATA_KEY] = marker

    return DataObject(
        layer.name,
        domain,
        {_FIELD_NAME: model_field},
        embeddings=[embedding],
        metadata=metadata,
    )


def layer_from_data_object(
    obj: DataObject, field: str | None = None
) -> Image | Labels:
    """Construct an Image or Labels layer from a compositional data object.

    Data sources other than :class:`ArraySource`, multiple embeddings,
    non-world embeddings, and irregular coordinate axes cannot yet be
    represented by this legacy layer projection. The domain intrinsic frame is
    index space; numeric axis values are annotations, so a markerless object
    carrying both numeric axis values and a world embedding is rejected as
    ambiguous until the Stage 2 coordinate graph can compose them explicitly.
    """
    from napari.layers import Image, Labels

    if not isinstance(obj, DataObject):
        raise TypeError('obj must be a DataObject')
    if not isinstance(obj.domain, StructuredGridDomain):
        raise TypeError(
            'layer_from_data_object requires a StructuredGridDomain'
        )
    model_field = _select_field(obj, field)
    model_field.require_full_grid(tuple(axis.name for axis in obj.domain.axes))
    if not isinstance(model_field.source, ArraySource):
        raise NotImplementedError(
            'DataObject fields not backed by ArraySource are not supported'
        )
    if np.issubdtype(model_field.dtype, np.complexfloating):
        raise NotImplementedError(
            'napari Image layers do not support complex data; view complex '
            'fields through derived magnitude or phase fields'
        )

    marker = _layer_marker(obj.metadata)
    metadata = _layer_metadata(obj.metadata, marker)

    if marker is None:
        embedding = _world_embedding(obj)
        if embedding is not None and _has_numeric_axis_values(obj):
            raise NotImplementedError(
                'numeric axis values and a world coordinate embedding are '
                'ambiguous because the intrinsic frame is index space; the '
                'Stage 2 coordinate graph will resolve their composition'
            )
        coordinate_scale, coordinate_translate = _regular_axis_transform(obj)
        kind = 'image'
        rgb = False
        transform = {
            'scale': coordinate_scale,
            'translate': coordinate_translate,
            'rotate': None,
            'shear': None,
            'affine': _embedding_affine(embedding, obj.domain.shape),
        }
        axis_labels = tuple(axis.name for axis in obj.domain.axes)
    else:
        kind = marker.get('kind')
        rgb = marker.get('rgb')
        if kind not in {'image', 'labels'} or not isinstance(rgb, bool):
            raise ValueError(
                "'napari:layer' metadata must name an image/labels kind and "
                'a boolean rgb flag'
            )
        transform = marker.get('transform')
        axis_labels = marker.get('axis_labels')
        if (
            not isinstance(transform, Mapping)
            or not all(
                key in transform
                for key in ('scale', 'translate', 'rotate', 'shear', 'affine')
            )
            or not _valid_axis_labels(axis_labels, obj.domain.shape)
        ):
            raise ValueError("'napari:layer' metadata is incomplete")
        embedding = _world_embedding(obj)
        if marker.get('embedding') is not embedding:
            axis_labels = tuple(axis.name for axis in obj.domain.axes)
            transform = {
                'scale': np.ones(len(obj.domain.shape)),
                'translate': np.zeros(len(obj.domain.shape)),
                'rotate': None,
                'shear': None,
                'affine': _embedding_affine(embedding, obj.domain.shape),
            }

    if kind == 'labels' and (rgb or model_field.component_axes):
        raise ValueError('Labels fields cannot have component axes')
    if rgb:
        expected_components = (len(model_field.shape) - 1,)
        if model_field.component_axes != expected_components:
            raise ValueError('RGB fields require one trailing component axis')
    elif model_field.component_axes:
        raise NotImplementedError(
            'non-RGB field component axes are not supported by Image layers'
        )

    common: dict[str, Any] = {
        'affine': transform['affine'],
        'axis_labels': axis_labels,
        'metadata': metadata,
        'name': obj.name,
        'rotate': transform['rotate'],
        'scale': transform['scale'],
        'shear': transform['shear'],
        'translate': transform['translate'],
        'units': tuple(axis.unit for axis in obj.domain.axes),
    }
    data = model_field.source.array
    if kind == 'labels':
        layer = Labels(data, **common)
        layer.editable = model_field.source.writable
        return layer
    contrast_limits = (
        marker.get('contrast_limits') if marker is not None else None
    )
    return Image(data, rgb=rgb, contrast_limits=contrast_limits, **common)


def _axis_names(labels: tuple[str, ...], ndim: int) -> tuple[str, ...]:
    if (
        len(labels) == ndim
        and all(isinstance(label, str) and label.strip() for label in labels)
        and len(set(labels)) == ndim
    ):
        return labels
    return tuple(f'dim_{index}' for index in range(ndim))


def _select_field(obj: DataObject, field_name: str | None) -> Field:
    if field_name is None:
        if len(obj.fields) != 1:
            raise ValueError(
                'field must be specified when a DataObject does not have '
                'exactly one field'
            )
        return next(iter(obj.fields.values()))
    try:
        return obj.fields[field_name]
    except KeyError as exc:
        raise KeyError(f'unknown field {field_name!r}') from exc


def _regular_axis_transform(
    obj: DataObject,
) -> tuple[np.ndarray, np.ndarray]:
    scale = np.ones(len(obj.domain.axes), dtype=float)
    translate = np.zeros(len(obj.domain.axes), dtype=float)
    for index, axis in enumerate(obj.domain.axes):
        if axis.values is None or axis.size == 0:
            continue
        if not np.issubdtype(axis.values.dtype, np.number):
            raise NotImplementedError(
                f'axis {axis.name!r} has non-numeric coordinate values'
            )
        values = axis.values.astype(float, copy=False)
        translate[index] = values[0]
        if axis.size == 1:
            continue
        differences = np.diff(values)
        if not np.allclose(
            differences,
            differences[0],
            rtol=_REGULAR_COORDINATE_RTOL,
            atol=_REGULAR_COORDINATE_ATOL,
        ):
            raise NotImplementedError(
                f'axis {axis.name!r} has irregular coordinate values'
            )
        if np.isclose(
            differences[0],
            0.0,
            rtol=_REGULAR_COORDINATE_RTOL,
            atol=_REGULAR_COORDINATE_ATOL,
        ):
            raise NotImplementedError(
                f'axis {axis.name!r} has zero coordinate spacing'
            )
        scale[index] = differences[0]
    return scale, translate


def _has_numeric_axis_values(obj: DataObject) -> bool:
    return any(
        axis.values is not None and np.issubdtype(axis.values.dtype, np.number)
        for axis in obj.domain.axes
    )


def _world_embedding(obj: DataObject) -> CoordinateEmbedding | None:
    if len(obj.embeddings) > 1:
        raise NotImplementedError(
            'multiple DataObject coordinate embeddings are not supported'
        )
    if not obj.embeddings:
        return None
    embedding = obj.embeddings[0]
    if embedding.target_frame.name != 'world':
        raise NotImplementedError(
            f'coordinate embedding target {embedding.target_frame.name!r} '
            "is not the supported 'world' frame"
        )
    ndim = len(obj.domain.shape)
    if embedding.matrix.shape != (ndim, ndim):
        raise NotImplementedError(
            'non-square coordinate embeddings are not supported by layers'
        )
    return embedding


def _embedding_affine(
    embedding: CoordinateEmbedding | None, shape: tuple[int, ...]
) -> np.ndarray:
    ndim = len(shape)
    affine = np.eye(ndim + 1, dtype=float)
    if embedding is not None:
        affine[:-1, :-1] = embedding.matrix
        affine[:-1, -1] = embedding.offset
    return affine


def _layer_marker(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    marker = metadata.get(_LAYER_METADATA_KEY)
    # ``metadata_key_present`` is written by this adapter. Generic dictionaries
    # under the reserved-looking key belong to the caller and stay untouched.
    if isinstance(marker, Mapping) and 'metadata_key_present' in marker:
        return marker
    return None


def _layer_metadata(
    metadata: Mapping[str, Any], marker: Mapping[str, Any] | None
) -> dict[str, Any]:
    restored = dict(metadata)
    if marker is None:
        return restored
    if marker.get('metadata_key_present'):
        restored[_LAYER_METADATA_KEY] = marker.get('metadata_key_value')
    else:
        restored.pop(_LAYER_METADATA_KEY, None)
    return restored


def _valid_axis_labels(labels: Any, shape: tuple[int, ...]) -> bool:
    return (
        isinstance(labels, tuple)
        and len(labels) == len(shape)
        and all(isinstance(label, str) for label in labels)
    )
