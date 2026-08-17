import numpy as np
import pytest
from scipy.spatial import cKDTree

from napari._vispy.layers import _points_viewport_culling
from napari.components.overlays import (
    BoundingBoxOverlay,
    CanvasOverlay,
    ScaleBarOverlay,
    SceneAxesOverlay,
)


def test_scene_overlays(qt_viewer):
    viewer = qt_viewer.viewer
    vispy_canvas = qt_viewer.canvas

    for overlay in viewer.scene.overlays.values():
        # vispy overlays only exist if they are visible at least once
        overlay.visible = True
        assert (
            vispy_canvas._viewer_overlay_to_visual[overlay][0].node
            in vispy_canvas.view.scene.children
        )

    old_vispy_scene_overlays = dict(
        vispy_canvas._viewer_overlay_to_visual.items()
    )

    new_overlay = SceneAxesOverlay(visible=True)
    viewer.scene.overlays.test = new_overlay

    assert new_overlay in vispy_canvas._viewer_overlay_to_visual
    new_overlay_node = vispy_canvas._viewer_overlay_to_visual[new_overlay][
        0
    ].node
    assert new_overlay_node in vispy_canvas.view.scene.children
    assert new_overlay_node not in vispy_canvas.view.children

    # old visuals should still be there, as they are reused when possible
    for _, vispy_overlays in old_vispy_scene_overlays.items():
        for vispy_overlay in vispy_overlays:
            assert vispy_overlay.node in vispy_canvas.view.scene.children

    viewer.scene.overlays.pop('test')
    assert new_overlay not in vispy_canvas._viewer_overlay_to_visual
    assert new_overlay_node not in vispy_canvas.view.children


def test_canvas_overlays(qt_viewer):
    canvas = qt_viewer.viewer.canvas
    vispy_canvas = qt_viewer.canvas

    for overlay in canvas.overlays.values():
        # vispy overlays only exist if they are visible at least once
        overlay.visible = True
        assert all(
            visual.node in vispy_canvas.view.children
            for visual in vispy_canvas._viewer_overlay_to_visual[overlay]
        )

    old_vispy_canvas_overlays = {
        k: list(v) for k, v in vispy_canvas._viewer_overlay_to_visual.items()
    }

    new_overlay = ScaleBarOverlay(visible=True)
    canvas.overlays.test = new_overlay

    assert new_overlay in vispy_canvas._viewer_overlay_to_visual
    new_overlay_node = vispy_canvas._viewer_overlay_to_visual[new_overlay][
        0
    ].node
    assert new_overlay_node not in vispy_canvas.view.scene.children
    assert new_overlay_node in vispy_canvas.view.children

    # old visuals should still be there, as they are reused when possible
    for _, vispy_overlays in old_vispy_canvas_overlays.items():
        for vispy_overlay in vispy_overlays:
            assert vispy_overlay.node in vispy_canvas.view.children

    canvas.overlays.pop('test')
    assert new_overlay not in vispy_canvas._viewer_overlay_to_visual
    assert new_overlay_node not in vispy_canvas.view.children


def test_layer_overlays(qt_viewer):
    viewer = qt_viewer.viewer
    canvas = qt_viewer.canvas

    view_children = len(canvas.view.children)
    scene_children = len(canvas.view.scene.children)

    assert not canvas._layer_overlay_to_visual

    layer = viewer.add_points()
    layer_node = canvas.layer_to_visual[layer].node

    for overlay in layer._overlays.values():
        # vispy overlays only exist if they are visible at least once
        overlay.visible = True
        if isinstance(overlay, CanvasOverlay):
            assert (
                canvas._layer_overlay_to_visual[layer][overlay].node
                in canvas.view.children
            )
        else:
            assert (
                canvas._layer_overlay_to_visual[layer][overlay].node
                in layer_node.children
            )

    old_vispy_overlays = {**canvas._layer_overlay_to_visual[layer]}

    new_overlay = BoundingBoxOverlay(visible=True)
    layer._overlays['test'] = new_overlay

    assert new_overlay in canvas._layer_overlay_to_visual[layer]
    new_overlay_node = canvas._layer_overlay_to_visual[layer][new_overlay].node
    assert new_overlay_node in layer_node.children
    assert new_overlay_node not in canvas.view.children

    # old visuals should still be there, as they are reused when possible
    for overlay, vispy_overlay in old_vispy_overlays.items():
        if isinstance(overlay, CanvasOverlay):
            assert vispy_overlay.node in canvas.view.children
        else:
            assert vispy_overlay.node in layer_node.children

    layer._overlays.pop('test')
    assert new_overlay not in canvas._layer_overlay_to_visual[layer]
    assert new_overlay_node not in canvas.view.children

    viewer.layers.pop()

    # should be back to the status quo
    assert not canvas._layer_overlay_to_visual
    assert len(canvas.view.children) == view_children
    assert len(canvas.view.scene.children) == scene_children


def test_points_viewbox_registry_tracks_large_layers(qt_viewer, monkeypatch):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    layer = qt_viewer.viewer.add_points(np.zeros((200, 2)))
    visual = qt_viewer.canvas.layer_to_visual[layer]

    assert visual in qt_viewer.canvas._viewbox_layers

    layer.data = np.zeros((50, 2))
    assert visual not in qt_viewer.canvas._viewbox_layers

    qt_viewer.viewer.layers.remove(layer)
    assert layer not in qt_viewer.canvas._viewbox_layer_callbacks


def test_points_marker_size_includes_layer_scale_once(qt_viewer):
    qt_viewer.show()
    layer = qt_viewer.viewer.add_points(
        [[0, 0]],
        size=10,
        scale=(1, 5),
        border_width=0,
        face_color='red',
        blending='opaque',
    )
    qt_viewer.viewer.scene.camera.center = (0, 0)
    qt_viewer.viewer.scene.camera.zoom = 1
    screenshot = qt_viewer.screenshot(flash=False)

    red = (
        screenshot[..., 0].astype(int) - screenshot[..., 1].astype(int) > 50
    ) & (screenshot[..., 0].astype(int) - screenshot[..., 2].astype(int) > 50)
    _, columns = np.nonzero(red)
    marker_width = np.ptp(columns) + 1
    device_pixel_ratio = qt_viewer.canvas.native.devicePixelRatioF()
    packed_size = layer.size[0] * abs(layer.scale[-1])

    assert marker_width == pytest.approx(
        packed_size * device_pixel_ratio, abs=2
    )


@pytest.mark.parametrize(
    'layer_kwargs',
    [
        {
            'size': 30,
            'border_width': 0.3,
            'shading': 'spherical',
            'blending': 'translucent',
        },
        {
            'size': 0.01,
            'border_width': 0,
            'canvas_size_limits': (12, 10000),
            'blending': 'opaque',
        },
        {
            'size': 10,
            'border_width': 3,
            'border_width_is_relative': False,
            'blending': 'minimum',
        },
        {
            'size': 2,
            'border_width': 0.1,
            'scale': (-2, 3),
            'translate': (100, -100),
            'blending': 'additive',
        },
    ],
    ids=[
        'spherical-relative',
        'clamped-opaque',
        'absolute-minimum',
        'transform-additive',
    ],
)
def test_points_viewport_culling_preserves_screenshot(
    qt_viewer, monkeypatch, layer_kwargs
):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    qt_viewer.show()
    row, col = np.mgrid[:101, :101]
    data = 100 * np.column_stack((row.ravel(), col.ravel())).astype(float)
    layer = qt_viewer.viewer.add_points(
        data,
        face_color=[1, 0, 0, 0.5],
        **layer_kwargs,
    )
    visual = qt_viewer.canvas.layer_to_visual[layer]
    culler = visual._viewport_culler
    monkeypatch.setattr(culler, '_start_index_build', lambda: None)
    qt_viewer.viewer.reset_view()
    qt_viewer.viewer.scene.camera.zoom *= 50
    base_center = np.asarray(qt_viewer.viewer.scene.camera.center)[-2:]
    qt_viewer.screenshot(flash=False)
    viewport_size = np.abs(
        np.diff(qt_viewer.canvas._viewbox_corners_in_world[:, -2:], axis=0)[0]
    )

    for offset in (np.zeros(2), 0.55 * viewport_size):
        qt_viewer.viewer.scene.camera.center = tuple(base_center + offset)
        culler._tree = None
        culler.restore_full_view()
        baseline = qt_viewer.screenshot(flash=False)
        culler._tree = cKDTree(data)
        culler._tree_generation = culler._generation
        culled = qt_viewer.screenshot(flash=False)

        assert culler._view_indices is not None
        assert len(culler._view_indices) < len(data)
        assert visual.node.points_markers.shared_program[
            'a_position'
        ].size == len(culler._view_indices)
        np.testing.assert_array_equal(culled, baseline)


def test_multiple_points_layers_preserve_culled_screenshot(
    qt_viewer, monkeypatch
):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    qt_viewer.show()
    row, col = np.mgrid[:101, :101]
    data = 100 * np.column_stack((row.ravel(), col.ravel())).astype(float)
    layers = [
        qt_viewer.viewer.add_points(
            data + offset, size=10, face_color=color, border_width=0
        )
        for offset, color in ((0, 'red'), (20, 'cyan'))
    ]
    cullers = [
        qt_viewer.canvas.layer_to_visual[layer]._viewport_culler
        for layer in layers
    ]
    for culler in cullers:
        monkeypatch.setattr(culler, '_start_index_build', lambda: None)

    qt_viewer.viewer.reset_view()
    qt_viewer.viewer.scene.camera.zoom *= 50
    baseline = qt_viewer.screenshot(flash=False)
    for culler, layer in zip(cullers, layers, strict=True):
        culler._tree = cKDTree(layer.data)
        culler._tree_generation = culler._generation
    culled = qt_viewer.screenshot(flash=False)

    assert all(culler._view_indices is not None for culler in cullers)
    np.testing.assert_array_equal(culled, baseline)


def test_points_culled_screenshot_after_payload_guard_drift(
    qt_viewer, monkeypatch
):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    qt_viewer.show()
    row, col = np.mgrid[:101, :101]
    data = 100 * np.column_stack((row.ravel(), col.ravel())).astype(float)
    layer = qt_viewer.viewer.add_points(
        data,
        size=0.01,
        scale=(1, 5),
        border_width=0,
        canvas_size_limits=(12, 10000),
        face_color='red',
        blending='opaque',
    )
    visual = qt_viewer.canvas.layer_to_visual[layer]
    culler = visual._viewport_culler
    monkeypatch.setattr(culler, '_start_index_build', lambda: None)
    qt_viewer.viewer.reset_view()
    qt_viewer.viewer.scene.camera.zoom *= 100
    qt_viewer.screenshot(flash=False)

    corners = qt_viewer.canvas._viewbox_corners_in_world[:, -2:]
    viewport_size = np.abs(np.diff(corners, axis=0)[0])
    canvas_size = np.asarray(qt_viewer.canvas._current_viewbox_size[::-1])
    base_center = np.asarray(qt_viewer.viewer.scene.camera.center)[-2:]
    drift = np.array([0, 0.499 * viewport_size[-1]])
    marker_world = (
        corners[1] + drift + np.array([0, 0.002 * viewport_size[-1]])
    )
    marker_data = layer.world_to_data(marker_world)
    layer.data = np.concatenate((layer.data, [marker_data]))

    lower, upper, _ = culler._data_view_bounds(corners, canvas_size)
    unpadded_payload_upper = upper + 0.5 * (upper - lower)
    assert marker_data[-1] > unpadded_payload_upper[-1]

    culler._tree = cKDTree(layer.data)
    culler._tree_generation = culler._generation
    qt_viewer.viewer.scene.camera.center = tuple(base_center)
    qt_viewer.screenshot(flash=False)
    indices = culler._view_indices
    assert indices is not None
    assert len(layer.data) - 1 in indices

    qt_viewer.viewer.scene.camera.center = tuple(base_center + drift)
    culled = qt_viewer.screenshot(flash=False)
    assert culler._view_indices is indices

    culler._tree = None
    baseline = qt_viewer.screenshot(flash=False)
    np.testing.assert_array_equal(culled, baseline)
    assert np.any(culled[..., 0] != culled[..., 1])


def test_grid_mode(qt_viewer):
    viewer = qt_viewer.viewer
    canvas = qt_viewer.canvas

    viewer.dims.ndisplay = 3
    viewer.add_image(np.ones((10, 10, 10)))

    angles = 10, 20, 30  # just some nonzero stuff
    zoom = 1
    viewer.scene.camera.angles = angles
    viewer.scene.camera.zoom = zoom

    canvas.on_draw(None)

    for camera in (canvas.camera, *canvas.grid_cameras):
        np.testing.assert_allclose(camera.angles, angles)
        assert camera.zoom == zoom

    # ensure that switching to grid maintains zoom and angles
    viewer.canvas.grid.enabled = True

    canvas.on_draw(None)

    for camera in (canvas.camera, *canvas.grid_cameras):
        np.testing.assert_allclose(camera.angles, angles)
        assert camera.zoom == zoom

    viewer.canvas.grid.enabled = False

    canvas.on_draw(None)

    for camera in (canvas.camera, *canvas.grid_cameras):
        np.testing.assert_allclose(camera.angles, angles)
        assert camera.zoom == zoom


def test_tiling_canvas_overlays(qt_viewer):
    viewer = qt_viewer.viewer
    canvas = qt_viewer.canvas

    viewer.canvas.overlays.scale_bar.visible = True
    viewer.canvas.overlays.text.visible = True
    viewer.canvas.overlays.text.text = 'test'
    viewer.canvas.overlays.scale_bar.position = 'bottom_left'
    viewer.canvas.overlays.text.position = 'bottom_left'

    vispy_scale_bar = canvas._viewer_overlay_to_visual[
        viewer.canvas.overlays.scale_bar
    ][0]
    vispy_text = canvas._viewer_overlay_to_visual[viewer.canvas.overlays.text][
        0
    ]

    padding = 10.0  # currently hardcoded
    y_max, x_max = canvas.size

    scale_bar_y_size = vispy_scale_bar.y_size + padding
    scale_bar_x_size = vispy_scale_bar.x_size + padding

    text_y_size = vispy_text.y_size + padding
    text_x_size = vispy_text.x_size + padding

    # check vertical tiling works on the bottom right
    viewer.canvas.overlays.scale_bar.position = 'bottom_right'
    viewer.canvas.overlays.text.position = 'bottom_right'
    canvas._update_overlay_canvas_positions()

    np.testing.assert_almost_equal(
        vispy_text.node.transform.translate[0],
        x_max - text_x_size,
        decimal=3,
    )
    np.testing.assert_almost_equal(
        vispy_text.node.transform.translate[1],
        y_max - text_y_size - scale_bar_y_size,
        decimal=3,
    )

    # move scale bar out of the way and check tiling is updated
    viewer.canvas.overlays.scale_bar.position = 'top_right'
    canvas._update_overlay_canvas_positions()
    np.testing.assert_almost_equal(
        vispy_text.node.transform.translate[0],
        x_max - text_x_size,
        decimal=3,
    )
    np.testing.assert_almost_equal(
        vispy_text.node.transform.translate[1],
        y_max - text_y_size,
        decimal=3,
    )

    # check horizontal tiling works on the top right
    viewer.canvas.overlays.text.position = 'top_right'
    canvas._update_overlay_canvas_positions()
    np.testing.assert_almost_equal(
        vispy_text.node.transform.translate[0],
        x_max - text_x_size - scale_bar_x_size,
        decimal=3,
    )
    np.testing.assert_almost_equal(
        vispy_text.node.transform.translate[1],
        0 + padding,
        decimal=3,
    )


def test_world_units_restored_after_removing_inconsistent_layer(qt_viewer):
    """Removing a units-inconsistent layer should re-enable unit-aware rendering.

    Regression test for #8771: when a layer with pixel (dimensionless) units is added to
    a viewer that already has length-unit layers, world units become inconsistent
    and unit-aware rendering is disabled.Upon removing the inconsistent layer,
    the canvas must call _update_world_units() on the next draw so the remaining compatible
    layers resume using the shared world units.
    """
    from pint import get_application_registry

    reg = get_application_registry()

    viewer = qt_viewer.viewer
    canvas = qt_viewer.canvas

    # Two images with compatible length units (um and nm share the same
    # dimensionality; consistent world units = nm, the smaller one).
    im1 = viewer.add_image(np.zeros((10, 10)), units=('um', 'um'))
    viewer.add_image(np.zeros((10, 10)), units=('nm', 'nm'))

    # Units should be consistent after adding compatible layers.
    assert viewer.layers.extent.units is not None
    vispy_im1 = canvas.layer_to_visual[im1]

    # the vispy layer received the shared world units (nm). (not just im1's own layer units (um))
    assert vispy_im1._world_units == (reg.nm, reg.nm)

    # Add layer with incompatible units (pixels)
    labels = viewer.add_labels(np.zeros((10, 10), dtype=int))

    # Units are now inconsistent across layers; _update_world_units() sets
    # world_units=None on each vispy layer, which causes the vispy layer's
    # _world_units to fall back to its own layer-local units.
    assert viewer.layers.extent.units is None
    assert vispy_im1._world_units == (
        reg.um,
        reg.um,
    )  # im1's own layer units, not (nm, nm)

    # Remove the incompatible layer.
    viewer.layers.remove(labels)

    canvas.on_draw(None)
    assert vispy_im1.world_units == (reg.nm, reg.nm)


def test_world_units_applied_to_inserted_layer_via_layerlist_event(qt_viewer):
    from pint import get_application_registry

    reg = get_application_registry()

    viewer = qt_viewer.viewer
    canvas = qt_viewer.canvas

    viewer.add_image(np.zeros((10, 10)), units=('um', 'um'))
    image_nm = viewer.add_image(np.zeros((10, 10)), units=('nm', 'nm'))

    assert viewer.layers.extent.units == (reg.nm, reg.nm)
    assert canvas.layer_to_visual[image_nm].world_units == (reg.nm, reg.nm)


def test_inserted_layer_receives_shared_world_units_when_units_unchanged(
    qt_viewer,
):
    from pint import get_application_registry

    reg = get_application_registry()

    viewer = qt_viewer.viewer
    canvas = qt_viewer.canvas

    viewer.add_image(np.zeros((10, 10)), units=('nm', 'nm'))
    image_um = viewer.add_image(np.zeros((10, 10)), units=('um', 'um'))

    assert viewer.layers.extent.units == (reg.nm, reg.nm)
    assert canvas.layer_to_visual[image_um].world_units == (reg.nm, reg.nm)
