from __future__ import annotations

from functools import partial
from typing import Any

import numpy as np
import pytest
from shapely.geometry import LineString

from napari.components import ViewerModel
from napari.components.overlays import SceneLineOverlay, SceneMeshOverlay
from napari.experimental._data_model import (
    ROI,
    CoordinateAxis,
    CoordinateEmbedding,
    CoordinateFrame,
    CoordinateSelection,
    DataObject,
    DerivedSource,
    Field,
    MPRController,
    MultiscaleSource,
    PlaneAnchor,
    Polygon,
    PolygonSetDomain,
    ROICollection,
    StructuredGridDomain,
    synthetic_mri,
)
from napari.experimental._data_model._tests.utils import (
    CountingSource,
    _three_dimensional_data_object,
)

_TRANSFORM_ATOL = 1e-12
_SPATIAL_AXES = (3, 4, 5)
_DISPLAYED_AXES = ((4, 5), (3, 5), (3, 4))


def _with_counting_magnitude(
    data_object: DataObject,
) -> tuple[DataObject, CountingSource]:
    signal_values = data_object.fields['signal'].source.array
    source = CountingSource(signal_values)
    counted = DataObject(
        data_object.name,
        data_object.domain,
        {
            'magnitude': Field(
                'magnitude', DerivedSource(source, np.abs), unit=None
            )
        },
        embeddings=data_object.embeddings,
        metadata=data_object.metadata,
    )
    return counted, source


def _length_one_axis_count(
    region: tuple[slice, ...], shape: tuple[int, ...]
) -> int:
    return sum(
        len(range(*item.indices(size))) == 1
        for item, size in zip(region, shape, strict=True)
    )


def _replace_embedding(
    data_object: DataObject,
    matrix: Any,
    *,
    offset: Any | None = None,
    target_axes: tuple[tuple[str, str], ...] = (
        ('z', 'mm'),
        ('y', 'mm'),
        ('x', 'mm'),
    ),
) -> DataObject:
    target = CoordinateFrame('other', target_axes)
    embedding = CoordinateEmbedding(
        data_object.domain,
        target,
        matrix,
        offset,
    )
    return data_object.replace(embeddings=(embedding,))


def _with_lps_embedding(
    data_object: DataObject,
    spatial_matrix_lps: Any,
    *,
    target_axis_order: tuple[str, str, str] = ('L', 'P', 'S'),
) -> DataObject:
    """Embed domain ``(z, y, x)`` axes using canonical LPS matrix rows."""
    canonical_axis = {'L': 0, 'P': 1, 'S': 2}
    matrix = np.zeros((3, len(data_object.domain.axes)))
    matrix[:, -3:] = np.asarray(spatial_matrix_lps)[
        [canonical_axis[name] for name in target_axis_order]
    ]
    return _replace_embedding(
        data_object,
        matrix,
        target_axes=tuple((name, 'mm') for name in target_axis_order),
    )


def _lps_data_object() -> DataObject:
    return _with_lps_embedding(
        synthetic_mri(),
        [[0.0, 0.0, 0.9], [0.0, 0.9, 0.0], [2.0, 0.0, 0.0]],
    )


def _make_lps_roi(
    data_object: DataObject,
    *,
    plane_axis: str = 'S',
    plane_value: float = 10.0,
    in_plane_axes: tuple[str, str] = ('L', 'P'),
    context: dict[str, object] | None = None,
    polygons: tuple[Polygon, ...] | None = None,
) -> ROI:
    if polygons is None:
        polygons = (
            Polygon(
                np.array([[2.0, 3.0], [6.0, 3.0], [6.0, 8.0], [2.0, 8.0]])
            ),
        )
    frame = data_object.embeddings[0].target_frame
    frame_axes = {axis.name: axis for axis in frame.axes}
    domain = PolygonSetDomain(
        polygons,
        in_plane_axes,
        tuple(frame_axes[name].unit for name in in_plane_axes),
    )
    geometry = DataObject('lesion geometry', domain)
    anchor = PlaneAnchor(
        frame,
        plane_axis,
        plane_value,
        in_plane_axes,
        {} if context is None else context,
    )
    return ROI('lesion', geometry, anchor, data_object)


def _triangle_soup_area(triangles: np.ndarray, axes: tuple[int, int]) -> float:
    vertices = triangles[..., list(axes)]
    first_edges = vertices[:, 1] - vertices[:, 0]
    second_edges = vertices[:, 2] - vertices[:, 0]
    cross_products = (
        first_edges[:, 0] * second_edges[:, 1]
        - first_edges[:, 1] * second_edges[:, 0]
    )
    return 0.5 * float(np.sum(np.abs(cross_products)))


def test_mpr_constructs_three_orthogonal_model_panes() -> None:
    controller = MPRController(synthetic_mri())

    assert controller.pane_names == ('z plane', 'y plane', 'x plane')
    assert len(controller.panes) == 3
    assert controller.async_slicing_active is False
    assert controller.streaming is None
    assert all(isinstance(pane, ViewerModel) for pane in controller.panes)
    assert tuple(pane.dims.displayed for pane in controller.panes) == (
        _DISPLAYED_AXES
    )
    for pane in controller.panes:
        assert pane.dims.axis_labels == (
            'time',
            'echo_time',
            'flip_angle',
            'z',
            'y',
            'x',
        )
        assert set(range(3)).issubset(pane.dims.not_displayed)
        assert all(pane.dims.nsteps[axis] > 1 for axis in range(3))
        assert [layer.name for layer in pane.layers] == [
            'synthetic MRI:magnitude'
        ]
        assert isinstance(pane.scene.overlays.crosshair, SceneLineOverlay)
        assert isinstance(pane.scene.overlays.rois, SceneLineOverlay)
        assert isinstance(pane.scene.overlays.roi_fill, SceneMeshOverlay)
        assert isinstance(pane.scene.overlays.roi_traces, SceneLineOverlay)
        assert len(pane.scene.overlays.rois.segments) == 0
        assert len(pane.scene.overlays.roi_fill.triangles) == 0
        assert len(pane.scene.overlays.roi_traces.segments) == 0


def test_mpr_async_slicing_falls_back_when_slicer_api_is_missing(
    monkeypatch,
) -> None:
    from napari.components._layer_slicer import _LayerSlicer

    monkeypatch.setattr(_LayerSlicer, 'wait_until_idle', None)

    controller = MPRController(synthetic_mri())

    assert controller.enable_async_slicing() is False
    assert controller.async_slicing_active is False
    assert all(
        slicer._force_sync is True for slicer in controller._pane_slicers
    )
    controller.close()


def test_mpr_default_slicing_updates_model_pane() -> None:
    fine = np.arange(8 * 6 * 4, dtype=np.uint16).reshape(8, 6, 4)
    coarse = fine[::2, ::2, ::2].copy()
    data_object = _three_dimensional_data_object(
        MultiscaleSource((fine, coarse))
    )
    controller = MPRController(data_object)

    assert controller.async_slicing_active is False
    assert all(
        slicer._force_sync is True for slicer in controller._pane_slicers
    )
    layer = controller.panes[0].layers[0]
    before = np.asarray(layer._data_view).copy()
    controller.panes[0].dims.set_current_step(0, 2)

    assert not np.array_equal(before, layer._data_view)
    np.testing.assert_array_equal(layer._data_view, coarse[1])
    controller.close()


class _RecordingSession:
    def __init__(self, source, *, fail_start: bool = False) -> None:
        self.view_source = source
        self.startup_complete = True
        self.on_startup_complete: list = []
        self.events: list[object] = []
        self._fail_start = fail_start

    def start(self) -> None:
        self.events.append('start')
        if self._fail_start:
            raise RuntimeError('synthetic start failure')

    def update(self, pane_index: int) -> None:
        self.events.append(('update', pane_index))

    def startup_status(self) -> str:
        return 'recording'

    def close(self) -> None:
        self.events.append('close')


def test_mpr_streaming_session_is_started_updated_and_closed() -> None:
    sessions: list[_RecordingSession] = []

    def factory(controller):
        assert not hasattr(controller, 'panes')
        sessions.append(_RecordingSession(controller._model_field.source))
        return sessions[-1]

    controller = MPRController(synthetic_mri(), streaming=factory)
    (session,) = sessions
    assert controller.streaming is session
    assert session.events == ['start']

    pane = controller.panes[0]
    pane.dims.set_current_step(0, (pane.dims.current_step[0] + 1) % 4)
    assert ('update', 0) in session.events

    controller.close()
    assert session.events[-1] == 'close'


def test_mpr_streaming_start_failure_closes_the_session() -> None:
    sessions: list[_RecordingSession] = []

    def factory(controller):
        sessions.append(
            _RecordingSession(controller._model_field.source, fail_start=True)
        )
        return sessions[-1]

    with pytest.raises(RuntimeError, match='synthetic start failure'):
        MPRController(synthetic_mri(), streaming=factory)

    assert sessions[0].events == ['start', 'close']


def test_mpr_enable_async_slicing_is_idempotent_and_closed_noop() -> None:
    controller = MPRController(synthetic_mri())

    assert controller.enable_async_slicing() is True
    assert controller.enable_async_slicing() is True
    assert controller.async_slicing_active is True
    assert all(
        slicer._force_sync is False for slicer in controller._pane_slicers
    )

    controller.close()

    assert controller.async_slicing_active is False
    assert controller.enable_async_slicing() is False


def test_mpr_initial_zoom_is_common_across_panes() -> None:
    controller = MPRController(synthetic_mri())

    assert len({pane.scene.camera.zoom for pane in controller.panes}) == 1

    controller.equalize_zoom(2.5)

    assert [pane.scene.camera.zoom for pane in controller.panes] == [
        2.5,
        2.5,
        2.5,
    ]
    with pytest.raises(TypeError, match='zoom must be a real number or None'):
        controller.equalize_zoom('2.5')  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='zoom must be positive and finite'):
        controller.equalize_zoom(0.0)


def test_mpr_links_zoom_once_by_default() -> None:
    controller = MPRController(synthetic_mri())
    call_counts = [0, 0, 0]

    for index, pane in enumerate(controller.panes):
        pane.scene.camera.events.zoom.connect(
            lambda _event, pane_index=index: call_counts.__setitem__(
                pane_index, call_counts[pane_index] + 1
            )
        )

    controller.panes[0].scene.camera.zoom = 2.5

    assert [pane.scene.camera.zoom for pane in controller.panes] == [
        2.5,
        2.5,
        2.5,
    ]
    assert call_counts == [1, 1, 1]


def test_mpr_can_disable_linked_zoom() -> None:
    controller = MPRController(synthetic_mri(), link_zoom=False)
    original = [pane.scene.camera.zoom for pane in controller.panes]

    controller.panes[0].scene.camera.zoom = original[0] * 2

    assert controller.panes[1].scene.camera.zoom == original[1]
    assert controller.panes[2].scene.camera.zoom == original[2]


def test_mpr_close_disconnects_zoom_link() -> None:
    controller = MPRController(synthetic_mri())
    original = controller.panes[1].scene.camera.zoom
    controller.close()

    controller.panes[0].scene.camera.zoom = original * 2

    assert controller.panes[1].scene.camera.zoom == original


def test_mpr_links_shared_camera_coordinates_for_reordered_lps_frame() -> None:
    data_object = _with_lps_embedding(
        synthetic_mri(),
        [[0.0, 0.0, -0.9], [0.0, -0.9, 0.0], [-2.0, 0.0, 0.0]],
        target_axis_order=('P', 'S', 'L'),
    )

    controller = MPRController(data_object)

    controller.panes[0].scene.camera.center = (0.0, 4.25, 5.75)

    assert controller.panes[1].scene.camera.center[2] == pytest.approx(5.75)
    assert controller.panes[2].scene.camera.center[2] == pytest.approx(4.25)


def test_mpr_links_shared_camera_center_components() -> None:
    controller = MPRController(_lps_data_object())
    coronal_s = controller.panes[1].scene.camera.center[1]
    sagittal_s = controller.panes[2].scene.camera.center[1]

    controller.panes[0].scene.camera.center = (0.0, 4.25, 5.75)

    assert controller.panes[1].scene.camera.center[2] == pytest.approx(5.75)
    assert controller.panes[2].scene.camera.center[2] == pytest.approx(4.25)
    assert controller.panes[1].scene.camera.center[1] == coronal_s
    assert controller.panes[2].scene.camera.center[1] == sagittal_s

    controller.panes[1].scene.camera.center = (0.0, 7.25, 8.75)

    assert controller.panes[0].scene.camera.center[2] == pytest.approx(8.75)
    assert controller.panes[2].scene.camera.center[1] == pytest.approx(7.25)


def test_mpr_can_disable_linked_pan() -> None:
    controller = MPRController(_lps_data_object(), link_pan=False)
    other_centers = [
        tuple(pane.scene.camera.center) for pane in controller.panes[1:]
    ]

    controller.panes[0].scene.camera.center = (0.0, 4.25, 5.75)

    assert [
        tuple(pane.scene.camera.center) for pane in controller.panes[1:]
    ] == other_centers


def test_mpr_close_disconnects_pan_link() -> None:
    controller = MPRController(_lps_data_object())
    other_centers = [
        tuple(pane.scene.camera.center) for pane in controller.panes[1:]
    ]
    controller.close()

    controller.panes[0].scene.camera.center = (0.0, 4.25, 5.75)

    assert [
        tuple(pane.scene.camera.center) for pane in controller.panes[1:]
    ] == other_centers


def test_mpr_displays_slice_pinned_roi_by_axis_name() -> None:
    data_object = _lps_data_object()
    roi = _make_lps_roi(data_object)
    collection = ROICollection(data_object)
    collection.add(roi)
    controller = MPRController(data_object, roi_collection=collection)

    controller.panes[0].dims.set_current_step(3, 5)

    axial_data = controller.panes[0].scene.overlays.rois.segments
    assert len(axial_data) == 4
    np.testing.assert_array_equal(
        axial_data[:, 0, 5], roi.data.domain.polygons[0].exterior[:, 0]
    )
    np.testing.assert_array_equal(
        axial_data[:, 0, 4], roi.data.domain.polygons[0].exterior[:, 1]
    )
    image_layer = controller.panes[0].layers[0]
    roi_world = axial_data[0, 0]
    image_world = np.asarray(
        image_layer.data_to_world(controller.panes[0].dims.current_step)
    )
    not_displayed = list(controller.panes[0].dims.not_displayed)
    np.testing.assert_allclose(
        roi_world[not_displayed],
        image_world[not_displayed],
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )
    assert len(controller.panes[1].scene.overlays.rois.segments) == 0
    assert len(controller.panes[2].scene.overlays.rois.segments) == 0
    face_color = controller.panes[0].scene.overlays.roi_fill.color
    edge_color = controller.panes[0].scene.overlays.rois.color
    np.testing.assert_allclose(face_color[:3], [0.0, 1.0, 1.0])
    assert 0.2 < face_color[3] < 0.3
    assert edge_color[3] == pytest.approx(1.0)


def test_mpr_displays_axial_roi_traces_in_crossing_panes() -> None:
    data_object = _lps_data_object()
    roi = _make_lps_roi(data_object)
    collection = ROICollection(data_object)
    collection.add(roi)
    controller = MPRController(data_object, roi_collection=collection)

    controller.set_cursor((4.5, 4.5, 10.0))

    assert len(controller.panes[0].scene.overlays.roi_traces.segments) == 0
    coronal_trace = controller.panes[1].scene.overlays.roi_traces.segments
    sagittal_trace = controller.panes[2].scene.overlays.roi_traces.segments
    assert len(coronal_trace) == 1
    assert len(sagittal_trace) == 1
    np.testing.assert_allclose(coronal_trace[0][:, 3], 10.0)
    np.testing.assert_allclose(coronal_trace[0][:, 5], [2.0, 6.0])
    np.testing.assert_allclose(sagittal_trace[0][:, 3], 10.0)
    np.testing.assert_allclose(sagittal_trace[0][:, 4], [3.0, 8.0])
    trace_overlay = controller.panes[1].scene.overlays.roi_traces
    np.testing.assert_allclose(trace_overlay.color[:3], [0.0, 1.0, 1.0])


def test_mpr_island_in_hole_fill_mesh_matches_union_area() -> None:
    data_object = _lps_data_object()
    outer = Polygon(
        np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]),
        (np.array([[2.0, 2.0], [8.0, 2.0], [8.0, 8.0], [2.0, 8.0]]),),
    )
    island = Polygon(
        np.array([[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0]])
    )
    roi = _make_lps_roi(data_object, polygons=(outer, island))
    collection = ROICollection(data_object)
    collection.add(roi)
    controller = MPRController(data_object, roi_collection=collection)

    controller.set_cursor((4.5, 4.5, 10.0))

    roi_mesh = controller.panes[0].scene.overlays.roi_fill
    assert len(roi_mesh.triangles) > 0
    assert _triangle_soup_area(roi_mesh.triangles, (4, 5)) == pytest.approx(
        roi.data.domain.as_multipolygon().area, rel=1e-6, abs=0.0
    )


def test_mpr_overlapping_components_fill_mesh_matches_union_area() -> None:
    data_object = _lps_data_object()
    first = Polygon(np.array([[0.0, 0.0], [6.0, 0.0], [6.0, 6.0], [0.0, 6.0]]))
    second = Polygon(
        np.array([[4.0, 0.0], [10.0, 0.0], [10.0, 6.0], [4.0, 6.0]])
    )
    roi = _make_lps_roi(data_object, polygons=(first, second))
    collection = ROICollection(data_object)
    collection.add(roi)
    controller = MPRController(data_object, roi_collection=collection)

    controller.set_cursor((4.5, 4.5, 10.0))

    roi_mesh = controller.panes[0].scene.overlays.roi_fill
    assert len(roi.data.domain.polygons) == 2
    assert len(roi_mesh.triangles) > 0
    assert _triangle_soup_area(roi_mesh.triangles, (4, 5)) == pytest.approx(
        roi.data.domain.as_multipolygon().area, rel=1e-6, abs=0.0
    )


def test_mpr_roi_trace_moves_with_crossing_slider_and_disappears() -> None:
    data_object = _lps_data_object()
    triangle = Polygon(np.array([[2.0, 2.0], [8.0, 2.0], [5.0, 8.0]]))
    roi = _make_lps_roi(data_object, polygons=(triangle,))
    collection = ROICollection(data_object)
    collection.add(roi)
    controller = MPRController(data_object, roi_collection=collection)
    coronal = controller.panes[1]

    controller.set_cursor((4.5, 2.7, 10.0))
    first_trace = coronal.scene.overlays.roi_traces.segments.copy()
    coronal.dims.set_current_step(4, 5)
    moved_trace = coronal.scene.overlays.roi_traces.segments.copy()
    coronal.dims.set_current_step(4, 20)

    assert not np.array_equal(first_trace[..., 5], moved_trace[..., 5])
    assert len(coronal.scene.overlays.roi_traces.segments) == 0


def test_mpr_roi_trace_hole_and_island_match_shapely_chords() -> None:
    data_object = _lps_data_object()
    outer = Polygon(
        np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]),
        (np.array([[2.0, 2.0], [8.0, 2.0], [8.0, 8.0], [2.0, 8.0]]),),
    )
    island = Polygon(
        np.array([[4.0, 4.0], [6.0, 4.0], [6.0, 6.0], [4.0, 6.0]])
    )
    roi = _make_lps_roi(data_object, polygons=(outer, island))
    collection = ROICollection(data_object)
    collection.add(roi)
    controller = MPRController(data_object, roi_collection=collection)

    controller.set_cursor((4.5, 4.5, 10.0))

    coronal_traces = controller.panes[1].scene.overlays.roi_traces.segments
    sagittal_traces = controller.panes[2].scene.overlays.roi_traces.segments
    assert len(coronal_traces) == 3
    assert len(sagittal_traces) == 3
    actual_intervals = sorted(
        tuple(sorted(trace[:, 5])) for trace in coronal_traces
    )
    oracle = roi.data.domain.as_multipolygon().intersection(
        LineString([(0.0, 4.5), (10.0, 4.5)])
    )
    expected_intervals = sorted(
        tuple(sorted(np.asarray(line.coords)[:, 0])) for line in oracle.geoms
    )
    np.testing.assert_allclose(actual_intervals, expected_intervals)


def test_mpr_roi_trace_draws_full_axis_parallel_boundary_edge() -> None:
    data_object = _lps_data_object()
    polygon = Polygon(
        np.array([[2.0, 2.7], [6.0, 2.7], [6.0, 8.1], [2.0, 8.1]])
    )
    roi = _make_lps_roi(data_object, polygons=(polygon,))
    collection = ROICollection(data_object)
    collection.add(roi)
    controller = MPRController(data_object, roi_collection=collection)

    controller.set_cursor((4.5, 2.7, 10.0))

    coronal_traces = controller.panes[1].scene.overlays.roi_traces.segments
    assert len(coronal_traces) == 1
    np.testing.assert_allclose(coronal_traces[0][:, 5], [2.0, 6.0])


def test_mpr_context_mismatched_roi_has_no_traces() -> None:
    data_object = _lps_data_object()
    roi = _make_lps_roi(data_object, context={'echo_time': 9.1})
    collection = ROICollection(data_object)
    collection.add(roi)
    controller = MPRController(data_object, roi_collection=collection)

    controller.set_cursor((4.5, 4.5, 10.0))

    assert all(
        len(pane.scene.overlays.roi_traces.segments) == 0
        for pane in controller.panes
    )


def test_mpr_roi_visibility_tracks_slice_context_and_collection() -> None:
    data_object = _lps_data_object()
    roi = _make_lps_roi(
        data_object,
        plane_value=9.1,
        context={'echo_time': 9.0},
    )
    collection = ROICollection(data_object)
    controller = MPRController(data_object, roi_collection=collection)
    axial = controller.panes[0]
    axial.dims.set_current_step(1, 2)
    axial.dims.set_current_step(3, 5)

    collection.add(roi)
    assert len(axial.scene.overlays.rois.segments) > 0

    axial.dims.set_current_step(3, 4)
    assert len(axial.scene.overlays.rois.segments) == 0
    axial.dims.set_current_step(3, 5)
    assert len(axial.scene.overlays.rois.segments) > 0

    axial.dims.set_current_step(1, 3)
    assert len(axial.scene.overlays.rois.segments) == 0
    axial.dims.set_current_step(1, 2)
    assert len(axial.scene.overlays.rois.segments) > 0

    collection.remove(roi)
    assert len(axial.scene.overlays.rois.segments) == 0

    mismatched_context = _make_lps_roi(
        data_object,
        context={'echo_time': 9.1},
    )
    collection.add(mismatched_context)
    assert len(axial.scene.overlays.rois.segments) == 0
    collection.remove(mismatched_context)

    just_inside = _make_lps_roi(data_object, plane_value=10.99)
    collection.add(just_inside)
    assert len(axial.scene.overlays.rois.segments) > 0
    collection.remove(just_inside)

    just_outside = _make_lps_roi(data_object, plane_value=11.01)
    collection.add(just_outside)
    assert len(axial.scene.overlays.rois.segments) == 0


def test_mpr_draw_capture_returns_polygon_to_model() -> None:
    data_object = _lps_data_object()
    collection = ROICollection(data_object)
    controller = MPRController(data_object, roi_collection=collection)
    axial = controller.panes[0]
    axial.dims.set_current_step((0, 1, 2, 3), (1, 2, 1, 5))
    draw_layer = controller.begin_roi_draw(0)
    vertices = np.tile(np.asarray(axial.dims.point), (4, 1))
    vertices[:, 4] = [1.0, 1.0, 5.0, 5.0]
    vertices[:, 5] = [2.0, 6.0, 6.0, 2.0]

    draw_layer.add_polygons(vertices)

    assert len(collection) == 1
    roi = next(iter(collection))
    assert roi.name == 'ROI 1'
    assert roi.anchor.frame is data_object.embeddings[0].target_frame
    assert roi.anchor.plane_axis == 'S'
    assert roi.anchor.plane_value == pytest.approx(10.0)
    assert roi.anchor.in_plane_axes == ('P', 'L')
    assert roi.anchor.context == {
        'time': 2.0,
        'echo_time': 9.0,
        'flip_angle': 15.0,
    }
    assert len(draw_layer.data) == 0
    assert len(axial.scene.overlays.rois.segments) > 0

    axial.dims.set_current_step(3, 4)
    assert len(axial.scene.overlays.rois.segments) == 0
    axial.dims.set_current_step(3, 5)
    assert len(axial.scene.overlays.rois.segments) > 0


def test_mpr_interactive_finish_captures_polygon() -> None:
    data_object = _lps_data_object()
    collection = ROICollection(data_object)
    controller = MPRController(data_object, roi_collection=collection)
    axial = controller.panes[0]
    draw_layer = controller.begin_roi_draw(0)
    vertices = np.tile(np.asarray(axial.dims.point), (4, 1))
    vertices[:, 4] = [1.0, 1.0, 5.0, 5.0]
    vertices[:, 5] = [2.0, 6.0, 6.0, 2.0]
    interactive_vertices = np.concatenate((vertices, vertices[-1:]))
    draw_layer.add(
        interactive_vertices,
        shape_type='polygon',
        gui=True,
    )
    draw_layer._is_creating = True
    draw_layer._moving_value = (0, len(interactive_vertices) - 1)

    draw_layer._finish_drawing()

    assert len(collection) == 1
    assert len(draw_layer.data) == 0
    assert len(axial.scene.overlays.rois.segments) > 0


def test_mpr_draw_capture_cleans_consecutive_duplicate_vertices() -> None:
    data_object = _lps_data_object()
    collection = ROICollection(data_object)
    controller = MPRController(data_object, roi_collection=collection)
    axial = controller.panes[0]
    draw_layer = controller.begin_roi_draw(0)
    vertices = np.tile(np.asarray(axial.dims.point), (5, 1))
    vertices[:, 4] = [1.0, 1.0, 1.0, 5.0, 5.0]
    vertices[:, 5] = [2.0, 2.0, 6.0, 6.0, 2.0]

    draw_layer.add_polygons(vertices)

    assert len(collection) == 1
    polygon = next(iter(collection)).data.domain.polygons[0]
    assert len(polygon.exterior) == 4
    assert len(draw_layer.data) == 0


def test_mpr_draw_capture_discards_degenerate_polygon(capsys) -> None:
    data_object = _lps_data_object()
    collection = ROICollection(data_object)
    controller = MPRController(data_object, roi_collection=collection)
    axial = controller.panes[0]
    draw_layer = controller.begin_roi_draw(0)
    vertices = np.tile(np.asarray(axial.dims.point), (4, 1))
    vertices[:, 4] = [1.0, 1.0, 5.0, 5.0]
    vertices[:, 5] = [2.0, 2.0, 6.0, 6.0]

    draw_layer.add_polygons(vertices)

    assert len(collection) == 0
    assert len(draw_layer.data) == 0
    warning_lines = capsys.readouterr().err.splitlines()
    assert len(warning_lines) == 1
    assert warning_lines[0].startswith(
        'ROI capture discarded invalid polygon:'
    )


def test_mpr_coronal_draw_uses_coronal_plane_axis() -> None:
    data_object = _lps_data_object()
    collection = ROICollection(data_object)
    controller = MPRController(data_object, roi_collection=collection)
    coronal = controller.panes[1]
    draw_layer = controller.begin_roi_draw(1)
    vertices = np.tile(np.asarray(coronal.dims.point), (4, 1))
    vertices[:, 3] = [2.0, 2.0, 6.0, 6.0]
    vertices[:, 5] = [1.0, 5.0, 5.0, 1.0]

    draw_layer.add_polygons(vertices)

    roi = next(iter(collection))
    assert roi.anchor.plane_axis == 'P'
    assert roi.anchor.in_plane_axes == ('S', 'L')


def test_mpr_roi_refresh_skips_unchanged_plane_and_context() -> None:
    controller = MPRController(synthetic_mri())
    source_pane = controller.panes[0]
    updates: list[str] = []
    for pane in controller.panes:
        pane.scene.overlays.rois.events.segments.connect(
            lambda: updates.append('rois')
        )
        pane.scene.overlays.roi_traces.events.segments.connect(
            lambda: updates.append('roi-traces')
        )

    source_pane.dims.set_current_step(3, 2)
    updates_after_move = updates.copy()
    source_pane.dims.events.current_step()
    source_pane.dims.events.current_step()

    assert updates_after_move == []
    assert updates == updates_after_move


def test_mpr_crosshairs_have_canvas_pixel_width() -> None:
    controller = MPRController(synthetic_mri())

    for pane in controller.panes:
        crosshair = pane.scene.overlays.crosshair
        assert 0 < crosshair.width <= 2


@pytest.mark.parametrize(
    ('target_axes', 'expected_names'),
    [
        (
            (('z', 'mm'), ('y', 'mm'), ('x', 'mm')),
            ('z plane', 'y plane', 'x plane'),
        ),
        (
            (('depth', 'mm'), ('row', 'mm'), ('column', 'mm')),
            ('depth plane', 'row plane', 'column plane'),
        ),
    ],
)
def test_mpr_derives_pane_names_from_target_frame(
    target_axes: tuple[tuple[str, str], ...],
    expected_names: tuple[str, str, str],
) -> None:
    data_object = synthetic_mri()
    renamed = _replace_embedding(
        data_object,
        data_object.embeddings[0].matrix,
        target_axes=target_axes,
    )

    controller = MPRController(renamed)

    assert controller.pane_names == expected_names


@pytest.mark.parametrize(
    ('spatial_matrix_lps', 'target_axis_order', 'expected_orders'),
    [
        pytest.param(
            [[0.0, 0.0, 0.9], [0.0, 0.9, 0.0], [2.0, 0.0, 0.0]],
            ('L', 'P', 'S'),
            (
                (0, 1, 2, 3, 4, 5),
                (0, 1, 2, 4, 3, 5),
                (0, 1, 2, 5, 3, 4),
            ),
            id='axial-positive-s-p-l',
        ),
        pytest.param(
            [[0.0, 0.0, 0.9], [2.0, 0.0, 0.0], [0.0, -0.9, 0.0]],
            ('L', 'P', 'S'),
            (
                (0, 1, 2, 4, 3, 5),
                (0, 1, 2, 3, 4, 5),
                (0, 1, 2, 5, 4, 3),
            ),
            id='coronal-positive-p-negative-s-positive-l',
        ),
        pytest.param(
            [[0.0, 0.0, -0.9], [0.0, -0.9, 0.0], [-2.0, 0.0, 0.0]],
            ('P', 'S', 'L'),
            (
                (0, 1, 2, 3, 4, 5),
                (0, 1, 2, 4, 3, 5),
                (0, 1, 2, 5, 3, 4),
            ),
            id='sign-flipped-reordered-target-axes',
        ),
    ],
)
def test_mpr_lps_panes_use_anatomical_layout_and_orientation(
    spatial_matrix_lps: list[list[float]],
    target_axis_order: tuple[str, str, str],
    expected_orders: tuple[tuple[int, ...], ...],
) -> None:
    data_object = _with_lps_embedding(
        synthetic_mri(),
        spatial_matrix_lps,
        target_axis_order=target_axis_order,
    )

    controller = MPRController(data_object)

    assert controller.pane_names == ('axial', 'coronal', 'sagittal')
    assert tuple(pane.dims.order for pane in controller.panes) == (
        expected_orders
    )
    assert tuple(
        tuple(axis.value for axis in pane.scene.camera.orientation2d)
        for pane in controller.panes
    ) == (('down', 'right'), ('up', 'right'), ('up', 'right'))

    # World coordinates must stay anatomical: the signed embedding row lands
    # in layer.scale and orientation2d alone flips the display. Dropping the
    # sign would mirror panes while leaving order and orientation2d intact.
    matrix = np.asarray(data_object.embeddings[0].matrix)
    for pane_index, pane in enumerate(controller.panes):
        layer = controller._image_layers[pane_index]
        for domain_axis in pane.dims.displayed:
            row = int(np.flatnonzero(matrix[:, domain_axis])[0])
            assert np.sign(layer.scale[domain_axis]) == np.sign(
                matrix[row, domain_axis]
            )


def test_mpr_synchronizes_non_spatial_steps_once() -> None:
    controller = MPRController(synthetic_mri())
    call_counts = [0, 0, 0]

    def count_event(pane_index: int, _event: Any) -> None:
        call_counts[pane_index] += 1

    callbacks = []
    for pane_index, pane in enumerate(controller.panes):
        callback = partial(count_event, pane_index)
        callbacks.append(callback)
        pane.dims.events.current_step.connect(callback)

    controller.panes[0].dims.set_current_step(1, 4)

    assert [pane.dims.current_step[1] for pane in controller.panes] == [
        4,
        4,
        4,
    ]
    assert call_counts == [1, 1, 1]


def test_mpr_cursor_rounds_clamps_and_round_trips() -> None:
    data_object = synthetic_mri()
    controller = MPRController(data_object)
    embedding = data_object.embeddings[0]
    continuous_indices = np.array([4.2, 10.4, 8.6])
    point = embedding.offset + np.array([2.0, 0.9, 0.9]) * (continuous_indices)

    controller.set_cursor(point)

    expected_indices = (4, 10, 9)
    assert (
        tuple(
            controller.panes[pane].dims.current_step[axis]
            for pane, axis in enumerate(_SPATIAL_AXES)
        )
        == expected_indices
    )
    np.testing.assert_allclose(
        controller.cursor,
        embedding.offset + np.array([2.0, 0.9, 0.9]) * expected_indices,
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )
    assert np.all(
        np.abs(controller.cursor - point)
        <= np.array([2.0, 0.9, 0.9]) / 2 + _TRANSFORM_ATOL
    )

    controller.set_cursor((-1e6, 1e6, -1e6))

    assert tuple(
        controller.panes[pane].dims.current_step[axis]
        for pane, axis in enumerate(_SPATIAL_AXES)
    ) == (0, 23, 0)
    np.testing.assert_allclose(
        controller.cursor,
        (-12.0, 10.7, 5.0),
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )
    assert all(
        pane.scene.overlays.crosshair.visible for pane in controller.panes
    )


def test_mpr_ignores_cursor_motion_within_current_voxel(monkeypatch) -> None:
    controller = MPRController(synthetic_mri())
    crosshair_updates = 0
    roi_updates = 0

    def count_crosshair_update() -> None:
        nonlocal crosshair_updates
        crosshair_updates += 1

    def count_roi_update(*, force: bool = False) -> None:
        nonlocal roi_updates
        roi_updates += 1

    monkeypatch.setattr(
        controller, '_update_crosshairs', count_crosshair_update
    )
    monkeypatch.setattr(controller, '_update_rois', count_roi_update)
    cursor = controller.cursor

    controller.set_cursor(cursor + np.array([0.4, 0.2, -0.2]))

    np.testing.assert_array_equal(controller.cursor, cursor)
    assert crosshair_updates == 0
    assert roi_updates == 0


def test_mpr_same_voxel_recenters_when_requested() -> None:
    controller = MPRController(
        _lps_data_object(), link_pan=False, center_on_cursor=True
    )
    cursor = controller.cursor.copy()
    for pane in controller.panes:
        pane.scene.camera.center = (0.0, 0.0, 0.0)

    controller.set_cursor(cursor)

    assert any(
        tuple(pane.scene.camera.center) != (0.0, 0.0, 0.0)
        for pane in controller.panes
    )


def test_mpr_cursor_change_submits_one_slice_per_pane(monkeypatch) -> None:
    controller = MPRController(synthetic_mri())
    submissions = [0, 0, 0]

    for pane_index, slicer in enumerate(controller._pane_slicers):

        def count_submit(*, index=pane_index, **_kwargs) -> None:
            submissions[index] += 1

        monkeypatch.setattr(slicer, 'submit', count_submit)

    cursor = controller.cursor + np.array([2.0, 0.9, 0.9])
    controller.set_cursor(cursor)

    assert submissions == [1, 1, 1]


def test_mpr_leaves_camera_centers_unchanged_by_default() -> None:
    controller = MPRController(_lps_data_object(), link_pan=False)
    original_centers = [
        tuple(pane.scene.camera.center) for pane in controller.panes
    ]

    controller.set_cursor((4.5, 5.4, 10.0))

    assert [
        tuple(pane.scene.camera.center) for pane in controller.panes
    ] == original_centers


def test_mpr_centers_every_pane_on_cursor_and_pane_click() -> None:
    controller = MPRController(
        _lps_data_object(), link_pan=False, center_on_cursor=True
    )

    controller.set_cursor((4.5, 5.4, 10.0))

    target_axis_by_name = {
        name: index for index, name in enumerate(controller._target_axis_names)
    }
    for pane_index, pane in enumerate(controller.panes):
        for (
            axis_name,
            component,
        ) in controller._camera_center_component_by_axis_name[
            pane_index
        ].items():
            assert pane.scene.camera.center[component] == pytest.approx(
                controller.cursor[target_axis_by_name[axis_name]]
            )

    clicked_target = np.array([3.6, 6.3, 12.0])
    viewer_position = np.asarray(controller.panes[0].dims.point)
    viewer_position[list(controller._spatial_axes)] = clicked_target
    controller.set_cursor_from_pane(0, viewer_position)

    np.testing.assert_allclose(controller.cursor, clicked_target)
    for pane_index, pane in enumerate(controller.panes):
        for (
            axis_name,
            component,
        ) in controller._camera_center_component_by_axis_name[
            pane_index
        ].items():
            assert pane.scene.camera.center[component] == pytest.approx(
                clicked_target[target_axis_by_name[axis_name]]
            )


def test_mpr_centers_every_pane_after_out_of_plane_slider_change() -> None:
    controller = MPRController(
        _lps_data_object(), link_pan=False, center_on_cursor=True
    )
    for pane in controller.panes:
        pane.scene.camera.center = (0.0, -100.0, -100.0)

    out_of_plane_axis = _SPATIAL_AXES[0]
    current_step = controller.panes[0].dims.current_step[out_of_plane_axis]
    new_step = 0 if current_step != 0 else 1
    controller.panes[0].dims.set_current_step(out_of_plane_axis, new_step)

    target_axis_by_name = {
        name: index for index, name in enumerate(controller._target_axis_names)
    }
    for pane_index, pane in enumerate(controller.panes):
        for (
            axis_name,
            component,
        ) in controller._camera_center_component_by_axis_name[
            pane_index
        ].items():
            assert pane.scene.camera.center[component] == pytest.approx(
                controller.cursor[target_axis_by_name[axis_name]]
            )


def test_mpr_coronal_lps_embedding_tracks_cursor_and_crosshairs() -> None:
    data_object = synthetic_mri()
    matrix = np.zeros((3, len(data_object.domain.axes)))
    matrix[0, 5] = 0.9
    matrix[1, 3] = 2.0
    matrix[2, 4] = -0.9
    permuted = _replace_embedding(
        data_object,
        matrix,
        offset=(5.0, -7.0, 11.0),
        target_axes=(('L', 'mm'), ('P', 'mm'), ('S', 'mm')),
    )

    controller = MPRController(permuted)
    target = np.array([7.7, -3.0, 7.4])
    controller.set_cursor(target)

    assert controller.pane_names == ('axial', 'coronal', 'sagittal')
    assert tuple(pane.dims.displayed for pane in controller.panes) == (
        (3, 5),
        (4, 5),
        (4, 3),
    )
    np.testing.assert_allclose(
        [
            controller.panes[pane].dims.point[axis]
            for pane, axis in enumerate((4, 3, 5))
        ],
        target[[2, 1, 0]],
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )
    np.testing.assert_allclose(
        controller.cursor, target, rtol=0.0, atol=_TRANSFORM_ATOL
    )
    expected_slice = CoordinateSelection(
        {'time': 1, 'echo_time': 2, 'flip_angle': 1, 'z': 2}
    ).read(permuted, 'magnitude')
    np.testing.assert_allclose(
        controller.panes[1].layers[0]._data_view,
        expected_slice,
        rtol=1e-6,
        atol=1e-7,
    )

    viewer_position = np.asarray(controller.panes[0].dims.point)
    viewer_position[[5, 3, 4]] = [11.3, -1.0, 3.8]
    controller.set_cursor_from_pane(0, viewer_position)

    np.testing.assert_allclose(
        controller.cursor,
        [11.3, -1.0, 3.8],
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )
    target_axis_for_domain_axis = {5: 0, 3: 1, 4: 2}
    for pane in controller.panes:
        displayed = pane.dims.displayed
        world_lines = pane.scene.overlays.crosshair.segments
        np.testing.assert_allclose(
            world_lines[0, :, displayed[1]],
            controller.cursor[target_axis_for_domain_axis[displayed[1]]],
            rtol=0.0,
            atol=1e-6,
        )
        np.testing.assert_allclose(
            world_lines[1, :, displayed[0]],
            controller.cursor[target_axis_for_domain_axis[displayed[0]]],
            rtol=0.0,
            atol=1e-6,
        )


def test_mpr_spatial_slider_updates_cursor_and_all_crosshairs() -> None:
    data_object = synthetic_mri()
    controller = MPRController(data_object)
    pane_index = 0
    spatial_axis = _SPATIAL_AXES[pane_index]
    new_step = 2
    original_crosshairs = [
        pane.scene.overlays.crosshair.segments.copy()
        for pane in controller.panes
    ]
    expected_cursor = controller.cursor.copy()
    expected_cursor[pane_index] = (
        data_object.embeddings[0].offset[pane_index]
        + data_object.embeddings[0].matrix[pane_index, spatial_axis] * new_step
    )

    controller.panes[pane_index].dims.set_current_step(spatial_axis, new_step)

    np.testing.assert_allclose(
        controller.cursor,
        expected_cursor,
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )
    for pane, original_data in zip(
        controller.panes, original_crosshairs, strict=True
    ):
        crosshair = pane.scene.overlays.crosshair
        assert not np.array_equal(crosshair.segments, original_data)


def test_mpr_sets_cursor_from_full_pane_world_position() -> None:
    controller = MPRController(synthetic_mri())
    target = np.array([-4.0, -1.0, 14.0])
    viewer_position = np.asarray(controller.panes[1].dims.point)
    viewer_position[list(_SPATIAL_AXES)] = target

    controller.set_cursor_from_pane(1, viewer_position)

    np.testing.assert_allclose(
        controller.cursor, target, rtol=0.0, atol=_TRANSFORM_ATOL
    )


def test_mpr_synchronizes_window_level_and_colormap() -> None:
    controller = MPRController(synthetic_mri())
    images = [pane.layers[0] for pane in controller.panes]
    crosshair_data = [
        pane.scene.overlays.crosshair.segments.copy()
        for pane in controller.panes
    ]

    images[0].contrast_limits = (0.1, 0.4)
    images[1].colormap = 'magma'

    assert [tuple(layer.contrast_limits) for layer in images] == [
        (0.1, 0.4),
        (0.1, 0.4),
        (0.1, 0.4),
    ]
    assert [layer.colormap.name for layer in images] == [
        'magma',
        'magma',
        'magma',
    ]
    for pane, original_data in zip(
        controller.panes, crosshair_data, strict=True
    ):
        np.testing.assert_array_equal(
            pane.scene.overlays.crosshair.segments, original_data
        )


def test_mpr_crosshairs_span_extent_and_cross_at_cursor() -> None:
    controller = MPRController(synthetic_mri())
    controller.set_cursor((-4.0, -1.0, 14.0))

    for pane in controller.panes:
        displayed = pane.dims.displayed
        world_lines = pane.scene.overlays.crosshair.segments
        image_extent = pane.layers[0].extent.world
        assert world_lines.shape == (2, 2, 6)
        np.testing.assert_allclose(
            world_lines[0, :, displayed[0]],
            image_extent[:, displayed[0]],
            rtol=0.0,
            atol=1e-6,
        )
        np.testing.assert_allclose(
            world_lines[1, :, displayed[1]],
            image_extent[:, displayed[1]],
            rtol=0.0,
            atol=1e-6,
        )
        spatial_target_indices = {
            domain_axis: target_axis
            for target_axis, domain_axis in enumerate(_SPATIAL_AXES)
        }
        np.testing.assert_allclose(
            world_lines[0, :, displayed[1]],
            controller.cursor[spatial_target_indices[displayed[1]]],
            rtol=0.0,
            atol=1e-6,
        )
        np.testing.assert_allclose(
            world_lines[1, :, displayed[0]],
            controller.cursor[spatial_target_indices[displayed[0]]],
            rtol=0.0,
            atol=1e-6,
        )

    controller.set_cursor((-4.0, -1.0, 5.0))
    pane = controller.panes[0]
    displayed = pane.dims.displayed
    lines = pane.scene.overlays.crosshair.segments
    image_extent = pane.layers[0].extent.world
    assert image_extent[0, displayed[1]] < lines[0, 0, displayed[1]]
    assert lines[0, 0, displayed[1]] < image_extent[1, displayed[1]]


def test_mpr_boundary_crosshairs_are_renderable_and_in_slice() -> None:
    controller = MPRController(synthetic_mri())
    controller.set_cursor((10.0, 5.0, 5.0))

    for pane in controller.panes:
        displayed = pane.dims.displayed
        lines = pane.scene.overlays.crosshair.segments
        extent = pane.layers[0].extent.world
        assert np.all(np.isfinite(lines))
        for line, constant_axis in zip(
            lines, reversed(displayed), strict=True
        ):
            assert extent[0, constant_axis] < line[0, constant_axis]
            assert line[0, constant_axis] < extent[1, constant_axis]


def test_mpr_displayed_slice_matches_coordinate_selection() -> None:
    data_object = synthetic_mri()
    controller = MPRController(data_object)
    controller.panes[0].dims.set_current_step((0, 1, 2), (1, 2, 1))
    controller.set_cursor((-4.0, -1.0, 14.0))

    expected = CoordinateSelection(
        {'time': 1, 'echo_time': 2, 'flip_angle': 1, 'z': 4}
    ).read(data_object, 'magnitude')

    np.testing.assert_allclose(
        controller.panes[0].layers[0]._data_view,
        expected,
        rtol=1e-6,
        atol=1e-7,
    )


def test_mpr_construction_and_cursor_moves_remain_lazy() -> None:
    counted, source = _with_counting_magnitude(synthetic_mri())

    controller = MPRController(counted)

    assert source.regions
    sample_regions = [
        region
        for region in source.regions
        if _length_one_axis_count(region, source.shape) == 0
    ]
    assert len(sample_regions) == 1
    assert all(
        len(range(*item.indices(size))) < size
        for item, size in zip(sample_regions[0], source.shape, strict=True)
        if size > 1
    )
    assert all(
        _length_one_axis_count(region, source.shape) in (0, 4)
        for region in source.regions
    )
    source.regions.clear()

    controller.set_cursor((-12.0, -10.0, 5.0))

    assert source.regions
    assert all(
        _length_one_axis_count(region, source.shape) == 4
        for region in source.regions
    )


def test_mpr_contrast_sample_uses_coarsest_level_index_space() -> None:
    finest = np.zeros((32, 32, 32), dtype=float)
    coarsest = np.arange(64, dtype=float).reshape((4, 4, 4))
    coarsest[0, 0, 0] = -999.0

    source = MultiscaleSource((finest, coarsest))
    domain = StructuredGridDomain(
        tuple(CoordinateAxis(name, 32, unit='mm') for name in ('z', 'y', 'x'))
    )
    target = CoordinateFrame(
        'scanner', (('z', 'mm'), ('y', 'mm'), ('x', 'mm'))
    )
    data_object = DataObject(
        'small MRI',
        domain,
        {'magnitude': Field('magnitude', source, missing_value=-999.0)},
        embeddings=(CoordinateEmbedding(domain, target, np.eye(3)),),
    )

    controller = MPRController(data_object)

    expected_limits = np.percentile(
        np.array([2.0, 8.0, 10.0, 32.0, 34.0, 40.0, 42.0]),
        (1.0, 99.0),
    )

    for pane in controller.panes:
        np.testing.assert_allclose(
            pane.layers[0].contrast_limits,
            expected_limits,
            rtol=0.0,
            atol=_TRANSFORM_ATOL,
        )


def test_mpr_contrast_limits_leave_most_displayed_pixels_unclipped() -> None:
    controller = MPRController(synthetic_mri())

    for cursor in (
        (-12.0, -10.0, 5.0),
        (2.0, -0.1, 14.9),
        (18.0, 10.7, 25.7),
    ):
        controller.set_cursor(cursor)
        for layer in controller._image_layers:
            displayed_slice = np.asarray(layer._data_view)
            lower, upper = layer.contrast_limits
            unclipped_fraction = np.mean(
                (displayed_slice > lower) & (displayed_slice < upper)
            )
            assert unclipped_fraction > 0.5


def test_mpr_close_disconnects_all_synchronization() -> None:
    controller = MPRController(synthetic_mri())
    controller.panes[0].dims.set_current_step(1, 0)
    images = [pane.layers[0] for pane in controller.panes]
    original_crosshairs = [
        pane.scene.overlays.crosshair.segments.copy()
        for pane in controller.panes
    ]
    original_limits = tuple(images[1].contrast_limits)
    controller.close()
    controller.close()

    controller.panes[0].dims.set_current_step(1, 4)
    controller.panes[0].dims.set_current_step(3, 0)
    images[0].contrast_limits = (0.1, 0.4)
    images[0].colormap = 'magma'

    assert [pane.dims.current_step[1] for pane in controller.panes] == [
        4,
        0,
        0,
    ]
    assert tuple(images[1].contrast_limits) == original_limits
    assert images[1].colormap.name != 'magma'
    for pane, original_data in zip(
        controller.panes, original_crosshairs, strict=True
    ):
        np.testing.assert_array_equal(
            pane.scene.overlays.crosshair.segments, original_data
        )


def test_mpr_close_disconnects_global_async_setting() -> None:
    from napari.settings import get_settings

    controller = MPRController(synthetic_mri())
    pane = controller.panes[0]
    slicer = controller._pane_slicers[0]
    settings = get_settings()
    controller.close()

    try:
        settings.experimental.events.async_(value=True)
        assert slicer._force_sync is True
        old_step = pane.dims.current_step[3]
        pane.dims.set_current_step(3, 0 if old_step != 0 else 1)
    finally:
        settings.experimental.events.async_(value=settings.experimental.async_)


@pytest.mark.parametrize('embeddings_count', [0, 2])
def test_mpr_requires_exactly_one_embedding(
    embeddings_count: int,
) -> None:
    data_object = synthetic_mri()
    embeddings = (data_object.embeddings[0],) * embeddings_count

    with pytest.raises(
        NotImplementedError, match='exactly one coordinate embedding'
    ):
        MPRController(data_object.replace(embeddings=embeddings))


def test_mpr_rejects_non_data_object() -> None:
    with pytest.raises(TypeError, match='obj must be a DataObject'):
        MPRController(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'link_pan': 1}, 'link_pan must be a boolean'),
        ({'center_on_cursor': 1}, 'center_on_cursor must be a boolean'),
        ({'async_slicing': 1}, 'async_slicing must be a boolean'),
        ({'streaming': 1}, 'streaming must be callable or None'),
    ],
)
def test_mpr_rejects_invalid_option_types(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        MPRController(synthetic_mri(), **kwargs)  # type: ignore[arg-type]


def test_mpr_requires_structured_grid_domain() -> None:
    polygon = Polygon(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]))
    data_object = DataObject(
        'geometry', PolygonSetDomain((polygon,), ('x', 'y'))
    )

    with pytest.raises(TypeError, match='requires a StructuredGridDomain'):
        MPRController(data_object)


def test_mpr_roi_collection_requires_controller_target_identity() -> None:
    data_object = synthetic_mri()
    different_target = synthetic_mri()

    with pytest.raises(ValueError, match='target must be obj by identity'):
        MPRController(
            data_object, roi_collection=ROICollection(different_target)
        )


def test_mpr_rejects_unknown_field() -> None:
    with pytest.raises(KeyError, match="unknown field 'missing'"):
        MPRController(synthetic_mri(), 'missing')


def test_mpr_requires_three_target_axes() -> None:
    data_object = synthetic_mri()
    matrix = data_object.embeddings[0].matrix[:2]
    two_dimensional = _replace_embedding(
        data_object,
        matrix,
        target_axes=(('y', 'mm'), ('x', 'mm')),
    )

    with pytest.raises(NotImplementedError, match='three-axis target frame'):
        MPRController(two_dimensional)


@pytest.mark.parametrize(
    'matrix',
    [
        [
            [0.0, 0.0, 0.0, 2.0, 0.25, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.9, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.9],
        ],
        [
            [0.0, 0.0, 0.0, 2.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 3.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.9],
        ],
        [
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.9, 0.0],
            [0.0, 0.0, 0.0, 0.0, 0.0, 0.9],
        ],
    ],
)
def test_mpr_rejects_coupled_embedding(
    matrix: list[list[float]],
) -> None:
    with pytest.raises(NotImplementedError, match='off-diagonal coupling'):
        MPRController(_replace_embedding(synthetic_mri(), matrix))


def test_mpr_rejects_complex_field_with_bridge_error() -> None:
    with pytest.raises(
        NotImplementedError,
        match=r'Image layers do not support complex data.*magnitude or phase',
    ):
        MPRController(synthetic_mri(), 'signal')


@pytest.mark.parametrize(
    ('value', 'match'),
    [
        ((1.0, 2.0), 'world_point must be a 3-vector'),
        ((1.0, np.nan, 3.0), 'world_point must contain only finite'),
    ],
)
def test_mpr_rejects_invalid_cursor_vectors(
    value: tuple[float, ...], match: str
) -> None:
    controller = MPRController(synthetic_mri())

    with pytest.raises(ValueError, match=match):
        controller.set_cursor(value)


def test_mpr_rejects_invalid_pane_index() -> None:
    controller = MPRController(synthetic_mri())
    position = np.zeros(6)

    with pytest.raises(TypeError, match='must be an integer'):
        controller.set_cursor_from_pane(1.5, position)  # type: ignore[arg-type]
    with pytest.raises(IndexError, match='out of range'):
        controller.set_cursor_from_pane(3, position)


@pytest.mark.parametrize(
    ('value', 'match'),
    [
        ((1.0, 2.0), 'viewer_position must be a 6-vector'),
        ((0.0, 0.0, 0.0, 0.0, np.inf, 0.0), 'only finite values'),
    ],
)
def test_mpr_rejects_invalid_pane_positions(
    value: tuple[float, ...], match: str
) -> None:
    controller = MPRController(synthetic_mri())

    with pytest.raises(ValueError, match=match):
        controller.set_cursor_from_pane(0, value)


def test_mpr_generic_permuted_sign_flipped_embedding_tracks_target_rows() -> (
    None
):
    # Non-LPS names keep the generic branch; this preserves the fallback
    # coverage the LPS conversion of the original test would otherwise drop.
    data_object = synthetic_mri()
    matrix = np.zeros((3, len(data_object.domain.axes)))
    matrix[0, 5] = 0.9
    matrix[1, 4] = -0.9
    matrix[2, 3] = 2.0
    permuted = _replace_embedding(
        data_object,
        matrix,
        offset=(5.0, -7.0, 11.0),
        target_axes=(('u', 'mm'), ('v', 'mm'), ('w', 'mm')),
    )

    controller = MPRController(permuted)
    target = np.array([7.7, -10.6, 15.0])
    controller.set_cursor(target)

    assert controller.pane_names == ('u plane', 'v plane', 'w plane')
    assert tuple(pane.dims.displayed for pane in controller.panes) == (
        (4, 3),
        (5, 3),
        (5, 4),
    )
    assert all(
        pane.scene.camera.orientation2d == ('down', 'right')
        for pane in controller.panes
    )
    np.testing.assert_allclose(
        [
            controller.panes[pane].dims.point[axis]
            for pane, axis in enumerate((5, 4, 3))
        ],
        target,
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )
    np.testing.assert_allclose(
        controller.cursor, target, rtol=0.0, atol=_TRANSFORM_ATOL
    )
    expected_slice = CoordinateSelection(
        {'time': 1, 'echo_time': 2, 'flip_angle': 1, 'y': 4}
    ).read(permuted, 'magnitude')
    np.testing.assert_allclose(
        controller.panes[1].layers[0]._data_view,
        expected_slice.T,
        rtol=1e-6,
        atol=1e-7,
    )

    viewer_position = np.asarray(controller.panes[0].dims.point)
    viewer_position[[5, 4, 3]] = [11.3, -11.5, 19.0]
    controller.set_cursor_from_pane(0, viewer_position)
    np.testing.assert_allclose(
        controller.cursor,
        [11.3, -11.5, 19.0],
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )


def test_mpr_panes_present_a_continuous_field_with_linear_interpolation() -> (
    None
):
    controller = MPRController(synthetic_mri())

    try:
        for pane in controller.panes:
            assert pane.layers[0].interpolation2d == 'linear'
    finally:
        controller.close()


