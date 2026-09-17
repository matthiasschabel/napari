"""Explicit scientific-object subscription for one independent Image consumer."""

from __future__ import annotations

import weakref
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any, Self

from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._domain import StructuredGridDomain
from napari.experimental._data_model._view_bridge import (
    _DATA_OBJECT_METADATA_KEY,
    _IRREGULAR_AXES_METADATA_KEY,
    _get_view_field,
    _is_labels_field,
    _layer_coordinates,
    _layer_interpolation,
    _SourceArray,
    add_to_viewer,
)

if TYPE_CHECKING:
    import numpy as np

    from napari.layers import Image


def _prepare(
    obj: DataObject, field: str
) -> tuple[
    _SourceArray, np.ndarray, np.ndarray, tuple[str | None, ...], Any, str
]:
    if not isinstance(obj, DataObject) or not isinstance(
        obj.domain, StructuredGridDomain
    ):
        raise TypeError('Image binding requires a structured-grid DataObject')
    model_field = _get_view_field(obj, field)
    if model_field.dtype.kind not in 'biuf':
        raise TypeError('Image binding requires real numeric storage')
    if len(model_field.shape) < 2:
        raise NotImplementedError(
            'Image binding requires at least two storage axes'
        )
    if any(size == 0 for size in model_field.shape):
        raise ValueError('Image binding requires nonempty storage axes')
    if model_field.source.levels != 1 or _is_labels_field(model_field):
        raise NotImplementedError(
            'Image binding requires a single-level scalar Image field'
        )
    scale, translate, units, irregular = _layer_coordinates(obj)
    interpolation = _layer_interpolation(model_field)
    return (
        _SourceArray(model_field.source),
        scale,
        translate,
        units,
        irregular,
        interpolation,
    )


class ImageBinding:
    """Bind one single-level scalar Image to explicit DataObject updates.

    Call on the GUI thread for live viewers. Descriptor updates are synchronous;
    source-buffer changes require invalidation followed by ``refresh()``. Changes
    to layer data never write back. Contrast, colormap, opacity and layer name
    belong to each consumer and survive refresh. Placement and scientific axis
    metadata are projected from the model. Refresh validates the new projection
    before applying it; it is not a transaction across frontend property events.
    Unsupported model states raise on each attempted publication and retain the
    prior projection. The binding stays subscribed: a later supported update
    recovers automatically. Callers may restore the model or dispose the consumer;
    notification errors include its object and field names.

    Keep the binding alive while in use. ``dispose()`` disconnects it, as does
    removal of its layer from an evented viewer LayerList. Disposal never closes
    the borrowed source or removes the layer. A plain list requires explicit
    disposal. Context management provides exception-safe cleanup.
    """

    def __init__(self, viewer: Any, obj: DataObject, field: str) -> None:
        _prepare(obj, field)
        with ExitStack() as cleanup:
            layer = add_to_viewer(viewer, obj, field=field)
            cleanup.callback(viewer.layers.remove, layer)
            self._obj = obj
            self._field = field
            self._rank = layer.ndim
            self._closed = True
            owner = weakref.ref(self)

            def release(_reference):
                if (binding := owner()) is not None:
                    binding.dispose()

            self._layer = weakref.ref(layer, release)

            def changed(_obj):
                if (binding := owner()) is not None:
                    try:
                        binding.refresh()
                    except Exception as exc:  # Add consumer context without suppressing the failure.
                        exc.add_note(
                            f'ImageBinding field {binding.field!r} on {binding.obj.name!r}; '
                            'restore a supported field or dispose this consumer'
                        )
                        raise

            removed = getattr(
                getattr(viewer.layers, 'events', None), 'removed', None
            )
            removed_ref = None if removed is None else weakref.ref(removed)

            unsubscribe = obj.subscribe(changed)
            cleanup.callback(unsubscribe)

            def on_removed(event):
                if (
                    binding := owner()
                ) is not None and event.value is binding._layer():
                    binding.dispose()

            def disconnect():
                unsubscribe()
                emitter = None if removed_ref is None else removed_ref()
                if emitter is not None:
                    emitter.disconnect(on_removed)

            self._finalizer = weakref.finalize(self, disconnect)
            self._finalizer.atexit = False
            self._closed = False
            cleanup.callback(self.dispose)
            if removed is not None:
                removed.connect(on_removed)
            cleanup.pop_all()

    @property
    def obj(self) -> DataObject:
        return self._obj

    @property
    def field(self) -> str:
        return self._field

    @property
    def layer(self) -> Image:
        """The consumer Image, if it has not been released by its owner."""
        layer = self._layer()
        if layer is None:
            raise RuntimeError('bound Image no longer exists')
        return layer

    @property
    def closed(self) -> bool:
        return self._closed

    def refresh(self) -> None:
        """Project current scientific state after validating the whole payload."""
        if self.closed:
            raise RuntimeError('Image binding is closed')
        data, scale, translate, units, irregular, interpolation = _prepare(
            self.obj, self.field
        )
        if len(data.shape) != self._rank:
            raise NotImplementedError(
                'Image binding updates must retain the storage rank'
            )
        layer = self.layer
        metadata = dict(layer.metadata)
        metadata[_DATA_OBJECT_METADATA_KEY] = self.obj
        metadata.pop(_IRREGULAR_AXES_METADATA_KEY, None)
        if irregular:
            metadata[_IRREGULAR_AXES_METADATA_KEY] = irregular
        layer.data = data
        layer.scale = scale
        layer.translate = translate
        layer.units = units
        layer.axis_labels = tuple(axis.name for axis in self.obj.domain.axes)
        layer.metadata = metadata
        layer.interpolation2d = interpolation

    def dispose(self) -> None:
        """Disconnect idempotently without closing shared storage."""
        if not self.closed:
            self._closed = True
            self._finalizer()

    def __enter__(self) -> Self:
        if self.closed:
            raise RuntimeError('Image binding is closed')
        return self

    def __exit__(self, *_exc: object) -> None:
        self.dispose()
