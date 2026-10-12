"""Headless model-level multiplanar reconstruction controller."""

from __future__ import annotations

import sys
from contextlib import suppress
from functools import partial, wraps
from numbers import Integral, Real
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from napari.components.overlays import SceneLineOverlay, SceneMeshOverlay
from napari.experimental._data_model._annotation import (
    ROI,
    PlaneAnchor,
    ROICollection,
)
from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._domain import StructuredGridDomain
from napari.experimental._data_model._geometry import (
    LineString,
    Polygon,
    PolygonSetDomain,
)
from napari.experimental._data_model._shapes_codec import (
    polygon_to_shape_vertices,
)
from napari.experimental._data_model._view_bridge import (
    _embedding_coordinates,
    _get_view_field,
    add_to_viewer,
)
from napari.layers.shapes._shapes_models.polygon import (
    Polygon as NapariPolygon,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from napari.experimental._data_model._field import Field
    from napari.experimental._data_model._source import DataSource
    from napari.layers import Image, Labels, Shapes
    from napari.utils.events import EventEmitter

_CONTRAST_PERCENTILES = (1.0, 99.0)
_CONTRAST_SAMPLE_AXIS_POINTS = 8
_CROSSHAIR_EXTENT_FRACTION = 300
_CROSSHAIR_LINE_WIDTH = 1.5
_ROI_LINE_WIDTH = 1.0
_ROI_TRACE_LINE_WIDTH = 1.0
_ROI_FACE_COLOR = '#00FFFF40'
_LPS_AXIS_NAMES = frozenset(('L', 'P', 'S'))
# Radiological canvas conventions (+L=left, +P=posterior, +S=superior):
#   axial:    canvas x runs R->L (L rightward), canvas y bottom->top is P->A
#   coronal:  canvas x runs R->L,               canvas y bottom->top is I->S
#   sagittal: canvas x runs A->P (P rightward), canvas y bottom->top is I->S
# Layer world coordinates are anatomical because _apply_embedding writes the
# signed embedding row into layer.scale, so orientation2d alone controls the
# on-screen direction: ('down', 'right') shows the vertical world coordinate
# increasing downward (axial: +P down, so A is on top). Only LPS frames are
# recognized; an RAS-named frame falls back to the generic layout.
# Rows: (pane name, out-of-plane axis, (vertical, horizontal), orientation2d).
_LPS_PANE_LAYOUT = (
    ('axial', 'S', ('P', 'L'), ('down', 'right')),
    ('coronal', 'P', ('S', 'L'), ('up', 'right')),
    ('sagittal', 'L', ('S', 'P'), ('up', 'right')),
)


def _line_components(geometry: Any) -> tuple[np.ndarray, ...]:
    if geometry.is_empty:
        return ()
    if geometry.geom_type == 'LineString':
        coordinates = np.asarray(geometry.coords, dtype=float)
        return (coordinates,) if len(coordinates) >= 2 else ()
    if geometry.geom_type in ('MultiLineString', 'GeometryCollection'):
        return tuple(
            component
            for part in geometry.geoms
            for component in _line_components(part)
        )
    return ()


class MPRStreamingSession(Protocol):
    """Background data supply for one controller, owned by that controller.

    A session is created by the controller's ``streaming`` factory before
    any pane exists. If the factory raises, it must release whatever it
    allocated; once it returns, the controller owns the session and closes
    it on :meth:`MPRController.close` or on a later construction failure.
    """

    view_source: DataSource
    """Source the pane layers read; may wrap the field's own source."""

    startup_complete: bool
    """Whether data the panes need before fine refinement is available."""

    on_startup_complete: list[Callable[[], None]]
    """Callbacks run, possibly on a worker thread, when startup completes."""

    def start(self) -> None:
        """Begin pane-dependent work; called once the panes exist."""

    def update(self, pane_index: int) -> None:
        """React to a navigation change originating in ``pane_index``."""

    def startup_status(self) -> str:
        """Short progress description for diagnostics."""

    def close(self) -> None:
        """Stop background work and release resources."""


def _close_streaming_on_initialization_failure(initializer):
    """Close the owned streaming session if controller construction raises."""

    @wraps(initializer)
    def guarded(controller, *args, **kwargs):
        try:
            return initializer(controller, *args, **kwargs)
        except BaseException:
            session = getattr(controller, 'streaming', None)
            if session is not None:
                with suppress(Exception):
                    session.close()
            raise

    return guarded


class MPRController:
    """Coordinate three orthogonal model-only views of one data object.

    Model-only panes are synchronous by default. Async pane navigation uses
    napari's private NAP-4 ``_LayerSlicer`` and requires one ``QtViewer`` per
    pane to deliver completed slices. :class:`MPRWidget` creates those viewers
    before enabling async slicing. If the private slicer interface is
    unavailable, all panes remain synchronous and
    :attr:`async_slicing_active` is false.

    A target frame is recognized as an LPS patient frame when its three axis
    names are exactly ``L``, ``P``, and ``S`` in any order. LPS panes use
    radiological axial, coronal, and sagittal canvas orientation. Other target
    frames retain target-axis pane order and name each pane after its
    out-of-plane axis.

    Crosshairs intersect at the cursor except at an extent boundary, where the
    crossing is clamped inward by half the line width to prevent clipping.

    Parameters
    ----------
    obj : DataObject
        Object with exactly one diagonal embedding into a three-axis frame.
    field : str
        Real-valued field to display in every pane.
    roi_collection : ROICollection or None
        Model-owned annotations attached to ``obj``.
    link_zoom : bool
        Connect every pane at construction so they keep the same physical
        zoom. Changing this attribute later does not reconfigure the links.
    link_pan : bool
        Connect pane pairs at construction so the center component along their
        shared world axis stays aligned. Changing this attribute later does
        not reconfigure the links.
    center_on_cursor : bool
        Recenter every pane on the linked cursor after cursor changes.
    async_slicing : bool or None
        Preference for non-blocking navigation, consulted by
        :class:`MPRWidget`. Async slice delivery requires a ``QtViewer`` for
        every pane, so construction never activates it and model-only
        controllers stay synchronous; ``False`` tells an attaching widget not
        to enable async, while ``None`` (the default) or ``True`` lets it.
        Napari's global experimental setting may change a pane's slicer state
        while the controller is open.
    streaming : callable or None
        Factory called with this controller before any pane exists, returning
        an :class:`MPRStreamingSession` or ``None``. ``None`` (the default)
        reads the field's source directly, which suits in-memory data.
    """

    @_close_streaming_on_initialization_failure
    def __init__(
        self,
        obj: DataObject,
        field: str = 'magnitude',
        roi_collection: ROICollection | None = None,
        *,
        link_zoom: bool = True,
        link_pan: bool = True,
        center_on_cursor: bool = False,
        async_slicing: bool | None = None,
        streaming: Callable[[MPRController], MPRStreamingSession | None]
        | None = None,
    ) -> None:
        from napari.components import ViewerModel

        if not isinstance(obj, DataObject):
            raise TypeError('obj must be a DataObject')
        if not isinstance(obj.domain, StructuredGridDomain):
            raise TypeError('MPRController requires a StructuredGridDomain')
        if roi_collection is not None and not isinstance(
            roi_collection, ROICollection
        ):
            raise TypeError('roi_collection must be an ROICollection or None')
        if roi_collection is not None and roi_collection.target is not obj:
            raise ValueError('roi_collection target must be obj by identity')
        if not isinstance(link_zoom, bool):
            raise TypeError('link_zoom must be a boolean')
        if not isinstance(link_pan, bool):
            raise TypeError('link_pan must be a boolean')
        if not isinstance(center_on_cursor, bool):
            raise TypeError('center_on_cursor must be a boolean')
        if async_slicing is not None and not isinstance(async_slicing, bool):
            raise TypeError('async_slicing must be a boolean or None')
        if streaming is not None and not callable(streaming):
            raise TypeError('streaming must be callable or None')
        model_field = _get_view_field(obj, field)
        if len(obj.embeddings) != 1:
            raise NotImplementedError(
                'MPR requires exactly one coordinate embedding'
            )
        embedding = obj.embeddings[0]
        if embedding.target_frame.ndim != 3:
            raise NotImplementedError(
                'MPR requires an embedding into a three-axis target frame'
            )
        target_axis_names = tuple(
            axis.name for axis in embedding.target_frame.axes
        )
        spatial_axes, spatial_scale, spatial_translate, _ = (
            _embedding_coordinates(embedding)
        )
        is_lps_patient_frame = set(target_axis_names) == _LPS_AXIS_NAMES
        if is_lps_patient_frame:
            target_axis_by_name = {
                name: axis for axis, name in enumerate(target_axis_names)
            }
            self.pane_names = tuple(
                pane_name for pane_name, *_ in _LPS_PANE_LAYOUT
            )
            target_axis_for_pane = tuple(
                target_axis_by_name[out_of_plane]
                for _, out_of_plane, _, _ in _LPS_PANE_LAYOUT
            )
            displayed_axes_by_pane = tuple(
                tuple(
                    spatial_axes[target_axis_by_name[axis_name]]
                    for axis_name in displayed_axis_names
                )
                for _, _, displayed_axis_names, _ in _LPS_PANE_LAYOUT
            )
            pane_orientations = tuple(
                orientation for *_, orientation in _LPS_PANE_LAYOUT
            )
        else:
            self.pane_names = tuple(
                f'{name} plane' for name in target_axis_names
            )
            target_axis_for_pane = tuple(range(3))
            displayed_axes_by_pane = tuple(
                tuple(
                    axis
                    for target_axis, axis in enumerate(spatial_axes)
                    if target_axis != pane_index
                )
                for pane_index in range(3)
            )
            pane_orientations = (None, None, None)

        pane_for_target_axis = [0, 0, 0]
        for pane_index, target_axis in enumerate(target_axis_for_pane):
            pane_for_target_axis[target_axis] = pane_index

        self._obj = obj
        self._model_field = model_field
        self._field_source = model_field.source
        self._embedding = embedding
        self.roi_collection = roi_collection
        self.link_zoom = link_zoom
        self.link_pan = link_pan
        self.center_on_cursor = center_on_cursor
        self.async_slicing = async_slicing
        self._spatial_axes = spatial_axes
        self._spatial_scale = spatial_scale
        self._spatial_translate = spatial_translate
        self._target_axis_names = target_axis_names
        self._target_axis_for_pane = target_axis_for_pane
        self._displayed_axes_by_pane = displayed_axes_by_pane
        target_axis_by_domain_axis = {
            domain_axis: target_axis
            for target_axis, domain_axis in enumerate(spatial_axes)
        }
        self._pane_in_plane_axis_names = tuple(
            tuple(
                target_axis_names[target_axis_by_domain_axis[domain_axis]]
                for domain_axis in displayed_axes
            )
            for displayed_axes in displayed_axes_by_pane
        )
        self._pane_for_target_axis = tuple(pane_for_target_axis)
        self._non_spatial_axes = tuple(
            axis
            for axis in range(len(obj.domain.axes))
            if axis not in spatial_axes
        )
        self._syncing_dims = False
        self._syncing_presentation = False
        self._syncing_zoom = False
        self._syncing_pan = False
        self._closed = False
        self.streaming: MPRStreamingSession | None = None
        # Sessions report refined regions here; see _on_progressive_arrival.
        self.on_progressive_update: list[
            Callable[[int, tuple[slice, ...]], None]
        ] = []
        self._connections: list[tuple[EventEmitter, Callable[..., None]]] = []
        self._roi_draw_layers: dict[int, Shapes] = {}
        self._next_roi_number = (
            len(roi_collection) + 1 if roi_collection is not None else 1
        )

        if streaming is not None:
            self.streaming = streaming(self)
        view_source = (
            model_field.source
            if self.streaming is None
            else self.streaming.view_source
        )

        panes = [ViewerModel() for _ in displayed_axes_by_pane]
        pane_slicers = tuple(
            getattr(pane, '_layer_slicer', None) for pane in panes
        )
        self.async_slicing_active = False
        self._pane_slicers = pane_slicers
        # Build all layers synchronously so construction has deterministic
        # presentation state regardless of the user's global async setting.
        for slicer in pane_slicers:
            if slicer is not None and hasattr(slicer, '_force_sync'):
                slicer._force_sync = True

        image_layers: list[Image | Labels] = []
        for pane_index, (pane, displayed_axes) in enumerate(
            zip(panes, displayed_axes_by_pane, strict=True)
        ):
            layer = add_to_viewer(pane, obj, field, source=view_source)
            pane.dims.order = (
                tuple(
                    axis
                    for axis in range(pane.dims.ndim)
                    if axis not in displayed_axes
                )
                + displayed_axes
            )
            orientation = pane_orientations[pane_index]
            if orientation is not None:
                pane.scene.camera.orientation2d = orientation
            image_layers.append(layer)

        self.panes = tuple(panes)
        self._image_layers = tuple(image_layers)
        source_ndim = len(model_field.source.shape)
        assert all(pane.dims.ndim == source_ndim for pane in self.panes), (
            'every MPR pane dims.ndim must equal the source dimensionality'
        )
        self._camera_center_component_by_axis_name = tuple(
            {
                target_axis_names[target_axis_by_domain_axis[domain_axis]]: (
                    center_component
                )
                for center_component, domain_axis in zip(
                    range(
                        len(pane.scene.camera.center) - pane.dims.ndisplay,
                        len(pane.scene.camera.center),
                    ),
                    pane.dims.order[-pane.dims.ndisplay :],
                    strict=True,
                )
            }
            for pane in self.panes
        )
        self._share_initial_presentation(model_field)
        self.equalize_zoom()
        self._crosshair_edge_widths = tuple(
            self._calculate_crosshair_edge_width(pane_index)
            for pane_index in range(len(self.panes))
        )

        crosshairs: list[SceneLineOverlay] = []
        for pane_index, pane in enumerate(self.panes):
            crosshair = SceneLineOverlay(
                segments=self._crosshair_segments(pane_index),
                color='yellow',
                width=_CROSSHAIR_LINE_WIDTH,
                visible=True,
                order=1_000_003,
            )
            pane.scene.overlays.crosshair = crosshair
            crosshairs.append(crosshair)
        self._crosshairs = tuple(crosshairs)

        roi_layers: list[SceneLineOverlay] = []
        roi_meshes: list[SceneMeshOverlay] = []
        for pane in self.panes:
            roi_layer = SceneLineOverlay(
                segments=np.empty((0, 2, pane.dims.ndim)),
                color='cyan',
                width=_ROI_LINE_WIDTH,
                visible=True,
                order=1_000_001,
            )
            roi_mesh = SceneMeshOverlay(
                triangles=np.empty((0, 3, pane.dims.ndim)),
                color=_ROI_FACE_COLOR,
                visible=True,
                order=1_000_000,
            )
            pane.scene.overlays.rois = roi_layer
            pane.scene.overlays.roi_fill = roi_mesh
            roi_layers.append(roi_layer)
            roi_meshes.append(roi_mesh)
        self._roi_layers = tuple(roi_layers)
        self._roi_meshes = tuple(roi_meshes)
        self._roi_refresh_keys: list[
            tuple[float, tuple[tuple[str, Any], ...]] | None
        ] = [None] * len(self.panes)

        roi_trace_layers: list[SceneLineOverlay] = []
        for pane in self.panes:
            trace_layer = SceneLineOverlay(
                segments=np.empty((0, 2, pane.dims.ndim)),
                color='cyan',
                width=_ROI_TRACE_LINE_WIDTH,
                visible=True,
                order=1_000_002,
            )
            pane.scene.overlays.roi_traces = trace_layer
            roi_trace_layers.append(trace_layer)
        self._roi_trace_layers = tuple(roi_trace_layers)
        self._roi_trace_refresh_keys: list[
            tuple[float, tuple[tuple[str, Any], ...]] | None
        ] = [None] * len(self.panes)

        for pane_index, pane in enumerate(self.panes):
            self._connect(
                pane.dims.events.current_step,
                partial(self._on_dims_change, pane_index),
            )
            if self.link_zoom:
                self._connect(
                    pane.scene.camera.events.zoom,
                    partial(self._on_zoom_change, pane_index),
                )
            if self.link_pan:
                self._connect(
                    pane.scene.camera.events.center,
                    partial(self._on_center_change, pane_index),
                )
        for pane_index, layer in enumerate(self._image_layers):
            if hasattr(layer.events, 'contrast_limits'):
                self._connect(
                    layer.events.contrast_limits,
                    partial(
                        self._on_presentation_change,
                        pane_index,
                        'contrast_limits',
                    ),
                )
            if hasattr(layer.events, 'colormap'):
                self._connect(
                    layer.events.colormap,
                    partial(
                        self._on_presentation_change,
                        pane_index,
                        'colormap',
                    ),
                )
        self._roi_collection_callback: Callable[[], None] | None = None
        if self.roi_collection is not None:
            self._roi_collection_callback = partial(
                self._update_rois, force=True
            )
            self.roi_collection.on_changed.append(
                self._roi_collection_callback
            )
        self._update_rois()
        if self.streaming is not None:
            self.streaming.start()

    @property
    def cursor(self) -> np.ndarray:
        """Current cursor in embedding target-frame coordinates."""
        target_point = np.empty(3, dtype=float)
        for target_axis, domain_axis in enumerate(self._spatial_axes):
            pane_index = self._pane_for_target_axis[target_axis]
            pane = self.panes[pane_index]
            target_point[target_axis] = self._convert_value(
                pane.dims.point[domain_axis],
                pane.dims.units[domain_axis],
                self._image_layers[pane_index].units[domain_axis],
            )
        return target_point

    def set_cursor(self, world_point: Any) -> None:
        """Set the linked cursor from a target-frame three-vector.

        The diagonal embedding is inverted for the three spatial coordinates.
        Continuous domain indices are rounded to the nearest index and clamped
        to the corresponding domain-axis extent.
        """
        point = self._validate_vector(world_point, 3, name='world_point')
        continuous_indices = (
            point - self._spatial_translate
        ) / self._spatial_scale
        spatial_indices = np.rint(continuous_indices).astype(int)
        spatial_shape = np.array(
            [self._obj.domain.axes[axis].size for axis in self._spatial_axes]
        )
        spatial_indices = np.clip(spatial_indices, 0, spatial_shape - 1)
        current_indices = np.rint(
            (self.cursor - self._spatial_translate) / self._spatial_scale
        )
        if np.array_equal(spatial_indices, current_indices):
            if self.center_on_cursor:
                self._center_panes_on_cursor()
            return

        self._syncing_dims = True
        try:
            for target_axis, (domain_axis, index) in enumerate(
                zip(self._spatial_axes, spatial_indices, strict=True)
            ):
                pane_index = self._pane_for_target_axis[target_axis]
                pane = self.panes[pane_index]
                target_position = (
                    self._spatial_translate[target_axis]
                    + self._spatial_scale[target_axis] * index
                )
                pane_position = self._convert_value(
                    target_position,
                    self._image_layers[pane_index].units[domain_axis],
                    pane.dims.units[domain_axis],
                )
                if pane.dims.point[domain_axis] != pane_position:
                    # The point event slices; block only its duplicate
                    # current_step route in ViewerModel.
                    with pane.dims.events.current_step.blocker(
                        pane._update_layers
                    ):
                        pane.dims.set_point(domain_axis, pane_position)
            self._update_crosshairs()
            self._update_rois()
            if self.center_on_cursor:
                self._center_panes_on_cursor()
        finally:
            self._syncing_dims = False

    def set_cursor_from_pane(
        self, pane_index: int, viewer_position: Any
    ) -> None:
        """Set the cursor from a pane's full-dimensional world position.

        Under the current diagonal view bridge, the position components on the
        spatial domain axes are already scanner-frame coordinates. They are
        reordered into target-frame order and still passed through
        :meth:`set_cursor`, keeping inversion in one place for a future bridge
        that supports more general embeddings.
        """
        self._validate_pane_index(pane_index)
        position = self._validate_vector(
            viewer_position,
            len(self._obj.domain.axes),
            name='viewer_position',
        )
        pane = self.panes[pane_index]
        target_point = np.array(
            [
                self._convert_value(
                    position[domain_axis],
                    pane.dims.units[domain_axis],
                    self._image_layers[pane_index].units[domain_axis],
                )
                for domain_axis in self._spatial_axes
            ]
        )
        self.set_cursor(target_point)

    def begin_roi_draw(self, pane_index: int) -> Shapes:
        """Put one pane's world-coordinate draw layer in polygon mode.

        Completed polygons are observed through the public Shapes ``data``
        event. Napari emits its ``added`` action only after ``add_polygons`` or
        interactive ``finish_drawing`` has installed the completed vertices.
        """
        from napari.layers import Shapes

        self._validate_pane_index(pane_index)
        if self._closed:
            raise RuntimeError(
                'cannot begin ROI drawing on a closed controller'
            )
        if self.roi_collection is None:
            raise RuntimeError('ROI drawing requires an ROICollection')

        layer = self._roi_draw_layers.get(pane_index)
        if layer is None:
            pane = self.panes[pane_index]
            overlay_scale, overlay_translate = self._overlay_transform(
                pane_index
            )
            layer = Shapes(
                ndim=pane.dims.ndim,
                axis_labels=pane.dims.axis_labels,
                edge_color='cyan',
                face_color='transparent',
                name='roi-draw',
                scale=overlay_scale,
                translate=overlay_translate,
                units=self._image_layers[pane_index].units,
            )
            pane.add_layer(layer)
            self._roi_draw_layers[pane_index] = layer
            self._connect(
                layer.events.data,
                partial(self._on_roi_draw_data, pane_index),
            )
        layer.visible = True
        layer.editable = True
        layer.mode = 'add_polygon'
        return layer

    def close(self) -> None:
        """Disconnect subscriptions and signal owned workers to stop."""
        if self._closed:
            return
        self._closed = True
        if (
            self.roi_collection is not None
            and self._roi_collection_callback is not None
            and self._roi_collection_callback in self.roi_collection.on_changed
        ):
            self.roi_collection.on_changed.remove(
                self._roi_collection_callback
            )
        for emitter, callback in self._connections:
            emitter.disconnect(callback)
        self._connections.clear()
        if self.streaming is not None:
            self.streaming.close()
        try:
            from napari.settings import get_settings

            async_emitter = get_settings().experimental.events.async_
        except Exception:  # noqa: BLE001 - close must tolerate API drift
            async_emitter = None
        if async_emitter is not None:
            for pane in self.panes:
                try:
                    handler = getattr(pane, '_update_async', None)
                    if callable(handler):
                        async_emitter.disconnect(handler)
                except Exception:  # noqa: BLE001 - close must not fail
                    pass
        for slicer in self._pane_slicers:
            if slicer is not None and hasattr(slicer, '_force_sync'):
                # Closed model panes remain inspectable and mutable without
                # trying to submit work to an executor that no longer exists.
                slicer._force_sync = True
            shutdown = getattr(slicer, 'shutdown', None)
            if callable(shutdown):
                shutdown()
        self.async_slicing_active = False

    def enable_async_slicing(self) -> bool:
        """Enable pane slicers after their ``QtViewer`` consumers exist.

        Returns false without changing slicers when the controller is closed
        or napari's private slicer interface is unavailable.
        """
        if self._closed:
            return False
        if self.async_slicing_active:
            return True
        slicer_api_available = all(
            slicer is not None
            and hasattr(slicer, '_force_sync')
            and callable(getattr(slicer, 'wait_until_idle', None))
            and callable(getattr(slicer, 'shutdown', None))
            for slicer in self._pane_slicers
        )
        if not slicer_api_available:
            return False
        for slicer in self._pane_slicers:
            slicer._force_sync = False
        self.async_slicing = True
        self.async_slicing_active = True
        return self.async_slicing_active

    def lock_to_coarsest(self) -> None:
        """Lock every multiscale image layer to its coarsest level.

        Coarse-locked layers keep draw-triggered level selection from
        issuing fine-level reads; pair with :meth:`unlock_levels`, which is
        safe to call unconditionally (try/finally friendly). The pair
        assumes it owns the locks: a caller-set ``locked_data_level`` on
        these layers is overwritten, and :meth:`unlock_levels` restores
        ``None``, not the prior value.
        """
        for layer in self._image_layers:
            if getattr(layer, 'multiscale', False):
                layer.locked_data_level = len(layer.level_shapes) - 1

    def unlock_levels(self) -> None:
        """Release any coarse level locks so viewport level selection resumes."""
        for layer in self._image_layers:
            if (
                getattr(layer, 'multiscale', False)
                and layer.locked_data_level is not None
            ):
                layer.locked_data_level = None

    def wait_until_idle(self, timeout: float | None = None) -> None:
        """Wait for every pane's NAP-4 slicing work to complete."""
        for slicer in self._pane_slicers:
            wait_until_idle = getattr(slicer, 'wait_until_idle', None)
            if callable(wait_until_idle):
                wait_until_idle(timeout=timeout)

    def _connect(
        self, emitter: EventEmitter, callback: Callable[..., None]
    ) -> None:
        emitter.connect(callback)
        self._connections.append((emitter, callback))

    def equalize_zoom(self, zoom: float | None = None) -> None:
        """Set every pane to one physical zoom without recursive syncing.

        Parameters
        ----------
        zoom : float or None
            Positive finite zoom to apply. The smallest current pane zoom is
            used when omitted.
        """
        if self._syncing_zoom:
            return
        if zoom is None:
            common_zoom = min(
                float(pane.scene.camera.zoom) for pane in self.panes
            )
        else:
            if isinstance(zoom, (bool, np.bool_)) or not isinstance(
                zoom, Real
            ):
                raise TypeError('zoom must be a real number or None')
            common_zoom = float(zoom)
            if not np.isfinite(common_zoom) or common_zoom <= 0:
                raise ValueError('zoom must be positive and finite')
        self._syncing_zoom = True
        try:
            for pane in self.panes:
                pane.scene.camera.zoom = common_zoom
        finally:
            self._syncing_zoom = False

    def _share_initial_presentation(self, model_field: Field) -> None:
        source = self._image_layers[0]
        if hasattr(source, 'contrast_limits'):
            contrast_limits = self._sampled_contrast_limits(model_field)
            for layer in self._image_layers:
                if hasattr(layer, 'contrast_limits'):
                    layer.contrast_limits = contrast_limits
        if hasattr(source, 'colormap'):
            for layer in self._image_layers[1:]:
                if hasattr(layer, 'colormap'):
                    layer.colormap = source.colormap

    @staticmethod
    def _sampled_contrast_limits(model_field: Field) -> tuple[float, float]:
        level = model_field.source.levels - 1
        region = tuple(
            slice(
                0,
                size,
                # Force a stride so small lazy sources are not fully materialized.
                max(2, int(np.ceil(size / _CONTRAST_SAMPLE_AXIS_POINTS))),
            )
            if size > 1
            else slice(0, 1)
            for size in model_field.source.level_shape(level)
        )
        sample = np.asarray(model_field.read(region, level=level))
        valid = np.isfinite(sample)
        if model_field.missing_value is not None:
            valid &= sample != model_field.missing_value
        sampled_values = sample[valid]
        if sampled_values.size == 0:
            return (0.0, 1.0)

        lower, upper = np.percentile(sampled_values, _CONTRAST_PERCENTILES)
        if lower == upper:
            lower, upper = np.min(sampled_values), np.max(sampled_values)
        if lower == upper:
            lower = min(lower, 0.0)
            upper = max(upper, 1.0)
        return (float(lower), float(upper))

    def _calculate_crosshair_edge_width(self, pane_index: int) -> float:
        pane = self.panes[pane_index]
        displayed = list(pane.dims.displayed)
        extent = self._image_layers[pane_index].extent.data[:, displayed]
        positive_extents = np.ptp(extent, axis=0)
        positive_extents = positive_extents[positive_extents > 0]
        if positive_extents.size == 0:
            return 1 / _CROSSHAIR_EXTENT_FRACTION
        return float(np.min(positive_extents) / _CROSSHAIR_EXTENT_FRACTION)

    def _on_dims_change(self, pane_index: int, event: Any) -> None:
        if self._closed or self._syncing_dims:
            return
        source_steps = self.panes[pane_index].dims.current_step
        self._syncing_dims = True
        try:
            for other_index, pane in enumerate(self.panes):
                if other_index == pane_index:
                    continue
                for axis in self._non_spatial_axes:
                    if pane.dims.current_step[axis] != source_steps[axis]:
                        pane.dims.set_current_step(axis, source_steps[axis])
            if self.streaming is not None:
                self.streaming.update(pane_index)
            self._update_crosshairs()
            self._update_rois()
            if self.center_on_cursor:
                self._center_panes_on_cursor()
        finally:
            self._syncing_dims = False

    def _on_progressive_arrival(
        self, level: int, region: tuple[slice, ...]
    ) -> None:
        if self._closed:
            return
        for callback in tuple(self.on_progressive_update):
            with suppress(Exception):
                callback(level, region)

    def _on_zoom_change(self, pane_index: int, event: Any) -> None:
        if self._closed or self._syncing_zoom:
            return
        self.equalize_zoom(self.panes[pane_index].scene.camera.zoom)

    def _on_center_change(self, pane_index: int, event: Any) -> None:
        if self._closed or self._syncing_pan:
            return
        source_mapping = self._camera_center_component_by_axis_name[pane_index]
        source_center = self.panes[pane_index].scene.camera.center
        self._syncing_pan = True
        try:
            for other_index, pane in enumerate(self.panes):
                if other_index == pane_index:
                    continue
                other_mapping = self._camera_center_component_by_axis_name[
                    other_index
                ]
                shared_axes = source_mapping.keys() & other_mapping.keys()
                assert len(shared_axes) == 1, (
                    'orthogonal pane pairs must share exactly one target axis'
                )
                shared_axis = next(iter(shared_axes))
                center = list(pane.scene.camera.center)
                center[other_mapping[shared_axis]] = source_center[
                    source_mapping[shared_axis]
                ]
                pane.scene.camera.center = center
        finally:
            self._syncing_pan = False

    def _center_panes_on_cursor(self) -> None:
        target_axis_by_name = {
            name: index for index, name in enumerate(self._target_axis_names)
        }
        cursor = self.cursor
        self._syncing_pan = True
        try:
            for pane_index, pane in enumerate(self.panes):
                center = list(pane.scene.camera.center)
                for (
                    axis_name,
                    component,
                ) in self._camera_center_component_by_axis_name[
                    pane_index
                ].items():
                    center[component] = cursor[target_axis_by_name[axis_name]]
                pane.scene.camera.center = center
        finally:
            self._syncing_pan = False

    def _on_presentation_change(
        self, pane_index: int, attribute: str, event: Any
    ) -> None:
        if self._closed or self._syncing_presentation:
            return
        source = self._image_layers[pane_index]
        value = getattr(source, attribute)
        if attribute == 'contrast_limits':
            value = tuple(value)
        self._syncing_presentation = True
        try:
            for other_index, layer in enumerate(self._image_layers):
                if other_index != pane_index and hasattr(layer, attribute):
                    setattr(layer, attribute, value)
        finally:
            self._syncing_presentation = False

    def _update_crosshairs(self) -> None:
        for pane_index, crosshair in enumerate(self._crosshairs):
            crosshair.segments = self._crosshair_segments(pane_index)

    def _update_rois(self, *, force: bool = False) -> None:
        if self._closed:
            return
        target_frame = self._embedding.target_frame
        target_axis_by_name = {
            name: index for index, name in enumerate(self._target_axis_names)
        }
        for pane_index, (layer, mesh) in enumerate(
            zip(self._roi_layers, self._roi_meshes, strict=True)
        ):
            target_axis = self._target_axis_for_pane[pane_index]
            plane_value = self.cursor[target_axis]
            context = self._current_context(pane_index)
            refresh_key = (plane_value, tuple(context.items()))
            if not force and self._roi_refresh_keys[pane_index] == refresh_key:
                continue

            visible_rois: tuple[ROI, ...] = ()
            if self.roi_collection is not None:
                visible_rois = self.roi_collection.visible_at(
                    self._target_axis_names[target_axis],
                    plane_value,
                    context,
                    tolerance=abs(self._spatial_scale[target_axis]) / 2,
                    frame=target_frame,
                )

            pane = self.panes[pane_index]
            pane_point = np.asarray(
                self._image_layers[pane_index].data_to_world(
                    pane.dims.current_step
                ),
                dtype=float,
            )
            line_data: list[np.ndarray] = []
            triangle_data: list[np.ndarray] = []
            for roi in visible_rois:
                for component in roi.data.domain.as_multipolygon().geoms:
                    polygon = Polygon(
                        np.asarray(component.exterior.coords)[:-1],
                        tuple(
                            np.asarray(interior.coords)[:-1]
                            for interior in component.interiors
                        ),
                    )
                    encoded = polygon_to_shape_vertices(polygon)
                    triangulated = NapariPolygon(
                        encoded.astype(np.float32, copy=False)
                    )
                    triangles_2d = triangulated._face_vertices[
                        triangulated._face_triangles
                    ]
                    triangles = np.tile(
                        pane_point, (*triangles_2d.shape[:2], 1)
                    )
                    for coordinate, axis_name in enumerate(
                        roi.anchor.in_plane_axes
                    ):
                        target_axis = target_axis_by_name[axis_name]
                        domain_axis = self._spatial_axes[target_axis]
                        triangles[..., domain_axis] = triangles_2d[
                            ..., coordinate
                        ]
                    triangle_data.append(triangles)
                    for ring in (polygon.exterior, *polygon.holes):
                        closed_ring = np.concatenate((ring, ring[:1]), axis=0)
                        segments_2d = np.stack(
                            (closed_ring[:-1], closed_ring[1:]), axis=1
                        )
                        segments = np.tile(
                            pane_point, (*segments_2d.shape[:2], 1)
                        )
                        for coordinate, axis_name in enumerate(
                            roi.anchor.in_plane_axes
                        ):
                            target_axis = target_axis_by_name[axis_name]
                            domain_axis = self._spatial_axes[target_axis]
                            segments[..., domain_axis] = segments_2d[
                                ..., coordinate
                            ]
                        line_data.append(segments)

            if line_data:
                layer.segments = np.concatenate(line_data)
            elif len(layer.segments):
                layer.segments = np.empty((0, 2, pane.dims.ndim))
            if triangle_data:
                mesh.triangles = np.concatenate(triangle_data)
            elif len(mesh.triangles):
                mesh.triangles = np.empty((0, 3, pane.dims.ndim))
            self._roi_refresh_keys[pane_index] = refresh_key
        self._update_roi_traces(force=force)

    def _update_roi_traces(self, *, force: bool = False) -> None:
        """Replace cross-plane ROI trace chords for the current sliders.

        A slider exactly on an axis-parallel edge draws that full edge as a
        chord. Fill and containment exclude the boundary, so this accepted
        cosmetic inconsistency is deliberately limited to the trace view.
        """
        target_frame = self._embedding.target_frame
        target_axis_by_name = {
            name: index for index, name in enumerate(self._target_axis_names)
        }
        cursor = self.cursor
        for pane_index, layer in enumerate(self._roi_trace_layers):
            crossing_target_axis = self._target_axis_for_pane[pane_index]
            crossing_axis_name = self._target_axis_names[crossing_target_axis]
            crossing_value = cursor[crossing_target_axis]
            context = self._current_context(pane_index)
            refresh_key = (crossing_value, tuple(context.items()))
            if (
                not force
                and self._roi_trace_refresh_keys[pane_index] == refresh_key
            ):
                continue

            pane = self.panes[pane_index]
            pane_point = np.asarray(
                self._image_layers[pane_index].data_to_world(
                    pane.dims.current_step
                ),
                dtype=float,
            )
            line_data: list[np.ndarray] = []
            if self.roi_collection is not None:
                for roi in self.roi_collection:
                    if (
                        roi.anchor.plane_axis == crossing_axis_name
                        or not roi.matches_context(context, frame=target_frame)
                    ):
                        continue
                    try:
                        crossing_coordinate = roi.anchor.in_plane_axes.index(
                            crossing_axis_name
                        )
                    except ValueError:
                        continue
                    shared_coordinate = 1 - crossing_coordinate
                    geometry = roi.data.domain.as_multipolygon()
                    if geometry.is_empty:
                        continue
                    min_x, min_y, max_x, max_y = geometry.bounds
                    endpoints = np.array(
                        [[min_x, min_y], [max_x, max_y]], dtype=float
                    )
                    endpoints[:, crossing_coordinate] = crossing_value
                    intersection = geometry.intersection(LineString(endpoints))
                    for chord in _line_components(intersection):
                        vertices = np.tile(pane_point, (len(chord), 1))
                        plane_target_axis = target_axis_by_name[
                            roi.anchor.plane_axis
                        ]
                        plane_domain_axis = self._spatial_axes[
                            plane_target_axis
                        ]
                        vertices[:, plane_domain_axis] = roi.anchor.plane_value
                        shared_axis_name = roi.anchor.in_plane_axes[
                            shared_coordinate
                        ]
                        shared_target_axis = target_axis_by_name[
                            shared_axis_name
                        ]
                        shared_domain_axis = self._spatial_axes[
                            shared_target_axis
                        ]
                        vertices[:, shared_domain_axis] = chord[
                            :, shared_coordinate
                        ]
                        line_data.append(
                            np.stack((vertices[:-1], vertices[1:]), axis=1)
                        )

            if line_data:
                layer.segments = np.concatenate(line_data)
            elif len(layer.segments):
                layer.segments = np.empty((0, 2, pane.dims.ndim))
            self._roi_trace_refresh_keys[pane_index] = refresh_key

    def _on_roi_draw_data(self, pane_index: int, event: Any) -> None:
        if self._closed or getattr(event, 'action', None) != 'added':
            return
        layer = self._roi_draw_layers[pane_index]
        if not layer.data or self.roi_collection is None:
            return

        data_indices = getattr(event, 'data_indices', (-1,))
        index = int(data_indices[-1]) if data_indices else -1
        if index < 0:
            index += len(layer.data)
        vertices = np.asarray(layer.data[index], dtype=float)
        displayed_axes = self._displayed_axes_by_pane[pane_index]
        ring = vertices[:, displayed_axes]
        keep_vertex = np.ones(len(ring), dtype=bool)
        keep_vertex[1:] = np.any(ring[1:] != ring[:-1], axis=1)
        try:
            polygon = Polygon(ring[keep_vertex])
        except ValueError as exc:
            capture_error: ValueError | None = exc
        else:
            capture_error = None
            in_plane_axes = self._pane_in_plane_axis_names[pane_index]
            target_axes = {
                axis.name: axis for axis in self._embedding.target_frame.axes
            }
            axis_units = tuple(
                target_axes[name].unit for name in in_plane_axes
            )
            roi_name = f'ROI {self._next_roi_number}'
            roi_data = DataObject(
                f'{roi_name} geometry',
                PolygonSetDomain((polygon,), in_plane_axes, axis_units),
            )
            target_axis = self._target_axis_for_pane[pane_index]
            anchor = PlaneAnchor(
                self._embedding.target_frame,
                self._target_axis_names[target_axis],
                self.cursor[target_axis],
                in_plane_axes,
                self._current_context(pane_index),
            )
            self.roi_collection.add(ROI(roi_name, roi_data, anchor, self._obj))
            self._next_roi_number += 1

        # _finish_drawing clears _moving_value before emitting ADDED. Clear
        # this flag so remove() cannot enter its nested finish path and index
        # self._data_view.shapes[None].
        if layer._is_creating:
            layer._is_creating = False
        layer.remove([index])
        layer.mode = 'add_polygon'
        if capture_error is not None:
            print(  # noqa: T201
                f'ROI capture discarded invalid polygon: {capture_error}',
                file=sys.stderr,
            )

    def _current_context(self, pane_index: int) -> dict[str, Any]:
        pane = self.panes[pane_index]
        context: dict[str, Any] = {}
        for domain_axis in self._non_spatial_axes:
            axis = self._obj.domain.axes[domain_axis]
            step = pane.dims.current_step[domain_axis]
            if axis.categorical_labels is not None:
                value: Any = axis.categorical_labels[step]
            elif axis.values is not None:
                value = axis.values[step]
                if isinstance(value, np.generic):
                    value = value.item()
            else:
                value = float(pane.dims.point[domain_axis])
            context[axis.name] = value
        return context

    def _overlay_transform(
        self, pane_index: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """Keep slice axes on the image grid and displayed axes in world."""
        image = self._image_layers[pane_index]
        scale = np.ones(image.ndim, dtype=float)
        translate = np.zeros(image.ndim, dtype=float)
        not_displayed = list(self.panes[pane_index].dims.not_displayed)
        scale[not_displayed] = np.asarray(image.scale)[not_displayed]
        translate[not_displayed] = np.asarray(image.translate)[not_displayed]
        return scale, translate

    def _crosshair_segments(self, pane_index: int) -> np.ndarray:
        pane = self.panes[pane_index]
        displayed_axes = pane.dims.displayed
        base = np.asarray(pane.dims.current_step, dtype=float)
        base[list(self._spatial_axes)] = np.rint(
            (self.cursor - self._spatial_translate) / self._spatial_scale
        )
        extent = self._image_layers[pane_index].extent.data
        # An extent-boundary line is half-clipped without this inward inset.
        inset = self._crosshair_edge_widths[pane_index] / 2
        for axis in displayed_axes:
            lower = extent[0, axis] + inset
            upper = extent[1, axis] - inset
            base[axis] = (
                np.clip(base[axis], lower, upper)
                if lower <= upper
                else np.mean(extent[:, axis])
            )

        first_line = np.tile(base, (2, 1))
        first_line[:, displayed_axes[0]] = extent[:, displayed_axes[0]]
        second_line = np.tile(base, (2, 1))
        second_line[:, displayed_axes[1]] = extent[:, displayed_axes[1]]
        data_segments = np.stack((first_line, second_line))
        image = self._image_layers[pane_index]
        return np.asarray(
            [
                [image.data_to_world(vertex) for vertex in segment]
                for segment in data_segments
            ],
            dtype=np.float32,
        )

    def _validate_pane_index(self, pane_index: int) -> None:
        if isinstance(pane_index, bool) or not isinstance(
            pane_index, Integral
        ):
            raise TypeError('pane_index must be an integer')
        if pane_index < 0 or pane_index >= len(self.panes):
            raise IndexError('pane_index is out of range')

    @staticmethod
    def _convert_value(value: float, from_unit: Any, to_unit: Any) -> float:
        return float((value * from_unit).to(to_unit).magnitude)

    @staticmethod
    def _validate_vector(value: Any, length: int, *, name: str) -> np.ndarray:
        point = np.asarray(value, dtype=float)
        if point.shape != (length,):
            raise ValueError(f'{name} must be a {length}-vector')
        if not np.all(np.isfinite(point)):
            raise ValueError(f'{name} must contain only finite values')
        return point
