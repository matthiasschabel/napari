import time
from threading import Event

import numpy as np
import pytest
from scipy.spatial import cKDTree

from napari._vispy.layers import _points_viewport_culling
from napari._vispy.layers.points import VispyPointsLayer
from napari._vispy.utils.qt_font import FontInfo
from napari._vispy.visuals.markers import Markers
from napari.components import Dims
from napari.layers import Points
from napari.layers.points._points_constants import Mode, PointsProjectionMode


def test_empty_point_visuals_are_hidden_until_data_is_added():
    layer = Points(text={'string': {'constant': 'label'}})
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())

    assert not vispy_layer.node.points_markers.visible
    assert not vispy_layer.node.text.visible

    layer.add([0, 0])
    assert vispy_layer.node.points_markers.visible
    assert vispy_layer.node.text.visible

    layer.data = np.empty((0, 2))
    assert not vispy_layer.node.points_markers.visible
    assert not vispy_layer.node.text.visible


def test_point_markers_are_hidden_only_while_the_slice_is_empty():
    layer = Points([[0, 0, 0]])
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())

    assert vispy_layer.node.points_markers.visible

    layer._slice_dims(Dims(ndim=3, point=(1, 0, 0)))
    assert vispy_layer.node.visible
    assert not vispy_layer.node.points_markers.visible

    layer._slice_dims(Dims(ndim=3, point=(0, 0, 0)))
    assert vispy_layer.node.points_markers.visible


def test_point_highlights_are_hidden_until_a_point_is_selected():
    layer = Points([[0, 0]])
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())
    highlight_nodes = (
        vispy_layer.node.selection_markers,
        vispy_layer.node.highlight_lines,
    )

    assert [node.visible for node in highlight_nodes] == [False, False]

    layer.mode = 'select'
    layer.selected_data = {0}
    assert [node.visible for node in highlight_nodes] == [True, False]

    layer.selected_data = set()
    assert [node.visible for node in highlight_nodes] == [False, False]

    layer._is_selecting = True
    layer._drag_box = np.array([[0, 0], [1, 1]])
    layer._set_highlight(force=True)
    assert [node.visible for node in highlight_nodes] == [False, True]

    layer._is_selecting = False
    layer._set_highlight(force=True)
    assert [node.visible for node in highlight_nodes] == [False, False]


@pytest.mark.parametrize('opacity', [0, 0.3, 0.7, 1])
def test_VispyPointsLayer(opacity):
    points = np.array([[100, 100], [200, 200], [300, 100]])
    layer = Points(points, size=30, opacity=opacity)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    assert visual.node.opacity == opacity


def test_remove_selected_with_derived_text():
    """See https://github.com/napari/napari/issues/3504"""
    points = np.random.rand(3, 2)
    properties = {'class': np.array(['A', 'B', 'C'])}
    layer = Points(points, text='class', properties=properties)
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())
    np.testing.assert_array_equal(vispy_layer.node.text.text, ['A', 'B', 'C'])

    layer.selected_data = {1}
    layer.remove_selected()

    np.testing.assert_array_equal(vispy_layer.node.text.text, ['A', 'C'])


def test_change_text_updates_node_string():
    points = np.random.rand(3, 2)
    properties = {
        'class': np.array(['A', 'B', 'C']),
        'name': np.array(['D', 'E', 'F']),
    }
    layer = Points(points, text='class', properties=properties)
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())
    np.testing.assert_array_equal(
        vispy_layer.node.text.text, properties['class']
    )

    layer.text = 'name'

    np.testing.assert_array_equal(
        vispy_layer.node.text.text, properties['name']
    )


def test_change_text_color_updates_node_color():
    points = np.random.rand(3, 2)
    properties = {'class': np.array(['A', 'B', 'C'])}
    text = {'string': 'class', 'color': [1, 0, 0]}
    layer = Points(points, text=text, properties=properties)
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())
    np.testing.assert_array_equal(vispy_layer.node.text.color.rgb, [[1, 0, 0]])

    layer.text.color = [0, 0, 1]

    np.testing.assert_array_equal(vispy_layer.node.text.color.rgb, [[0, 0, 1]])


def test_change_properties_updates_node_strings():
    points = np.random.rand(3, 2)
    properties = {'class': np.array(['A', 'B', 'C'])}
    layer = Points(points, properties=properties, text='class')
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())
    np.testing.assert_array_equal(vispy_layer.node.text.text, ['A', 'B', 'C'])

    layer.properties = {'class': np.array(['D', 'E', 'F'])}

    np.testing.assert_array_equal(vispy_layer.node.text.text, ['D', 'E', 'F'])


def test_update_property_value_then_refresh_text_updates_node_strings():
    points = np.random.rand(3, 2)
    properties = {'class': np.array(['A', 'B', 'C'])}
    layer = Points(points, properties=properties, text='class')
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())
    np.testing.assert_array_equal(vispy_layer.node.text.text, ['A', 'B', 'C'])

    layer.properties['class'][1] = 'D'
    layer.refresh_text()

    np.testing.assert_array_equal(vispy_layer.node.text.text, ['A', 'D', 'C'])


def test_change_canvas_size_limits():
    points = np.random.rand(3, 2)
    layer = Points(points, canvas_size_limits=(0, 10000))
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())
    node = vispy_layer.node

    assert node.canvas_size_limits == (0, 10000)
    layer.canvas_size_limits = (20, 80)
    assert node.canvas_size_limits == (20, 80)


def test_text_with_non_empty_constant_string():
    points = np.random.rand(3, 2)
    layer = Points(points, text={'string': {'constant': 'a'}})

    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())

    # Vispy cannot broadcast a constant string and assert_array_equal
    # automatically broadcasts, so explicitly check length.
    assert len(vispy_layer.node.text.text) == 3
    np.testing.assert_array_equal(vispy_layer.node.text.text, ['a', 'a', 'a'])

    # Ensure we do position calculation for constants.
    # See https://github.com/napari/napari/issues/5378
    # We want row, column coordinates so drop 3rd dimension and flip.
    actual_position = vispy_layer.node.text.pos[:, 1::-1]
    np.testing.assert_allclose(actual_position, points)


def test_change_antialiasing():
    """Changing antialiasing on the layer should change it on the vispy node."""
    points = np.random.rand(3, 2)
    layer = Points(points)
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())
    layer.antialiasing = 5
    assert vispy_layer.node.antialias == layer.antialiasing


@pytest.mark.parametrize('scale', [(-1, -1), (1, -1), (-1, 1)])
def test_negative_scale_highlight(scale):
    """Negative layer scale must not produce negative sizes/widths.

    A negative scale is sometimes used to flip axes (and can be inherited
    from an image layer); vispy rejects negative ``edge_width``, so adding
    or selecting a point used to raise ValueError.
    """
    layer = Points(np.zeros((0, 2)), size=10, scale=scale)
    layer.border_width_is_relative = False
    layer.border_width = 1.0

    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())

    # previously raised ValueError: edge_width cannot be negative
    layer.add([10, 10])

    for markers in (
        vispy_layer.node.points_markers,
        vispy_layer.node.selection_markers,
    ):
        assert np.all(markers._data['a_size'] > 0)
        assert np.all(markers._data['a_edgewidth'] >= 0)


def test_highlight_with_rescale_projection():
    """Highlight should work when projection is 'rescale_linear'.

    Regression test for a bug where _view_size_scale (array for all view
    points) was multiplied with size indexed only by highlighted points,
    causing a shape mismatch when more than one point was in view but only
    a subset was highlighted.
    """
    # Place 5 points at known z positions, with a large size so all 5 spill
    # into the z=50 slice and _view_size_scale becomes a (5,) array.
    data = np.array(
        [[0, 0, 0], [25, 0, 0], [50, 0, 0], [75, 0, 0], [100, 0, 0]],
        dtype=float,
    )
    layer = Points(data, size=200)
    vispy_layer = VispyPointsLayer(layer, font_info=FontInfo())

    # Select point 0 BEFORE slicing so update_selected_view populates
    # _selected_view and _set_highlight populates _highlight_index.
    layer.selected_data = {0}
    layer.projection_mode = PointsProjectionMode.RESCALE_LINEAR
    layer._slice_dims(
        Dims(
            ndim=3,
            point=(50, 0, 0),
            margin_left=(100, 0, 0),
            margin_right=(100, 0, 0),
        )
    )

    # Verify the preconditions that cause the bug:
    # all 5 points in view, scale is a per-point array, only 1 highlighted
    assert len(layer._view_indices) == 5
    assert isinstance(layer._view_size, np.ndarray)
    assert len(layer._highlight_index) == 1

    # Previously, raised ValueError: could not broadcast input array from shape (5,) into shape (1,)
    vispy_layer._on_highlight_change()


@pytest.mark.parametrize('method', ['instanced', 'points'])
def test_markers_view_indices_reuse_packed_payload(method):
    markers = Markers(method=method)
    positions = np.array([[0, 1], [2, 3], [4, 5]], dtype=float)
    markers.set_data(
        positions,
        size=[5, 6, 7],
        edge_width=[1, 2, 3],
        edge_color=['red', 'green', 'blue'],
        face_color=['white', 'black', 'yellow'],
        symbol=['o', 's', '*'],
    )
    full_data = markers._full_data
    full_bounds = [markers._compute_bounds(axis, None) for axis in (0, 1)]

    markers.set_view_indices(np.array([0, 2]))

    assert markers._full_data is full_data
    np.testing.assert_array_equal(markers._data, full_data[[0, 2]])
    assert [
        markers._compute_bounds(axis, None) for axis in (0, 1)
    ] == full_bounds
    divisor = 1 if method == 'instanced' else None
    assert all(
        markers.shared_program[name].divisor == divisor
        for name in markers._data.dtype.names
    )
    assert markers.shared_program['a_position'].size == 2
    view_vbo = markers._view_vbo

    markers.set_view_indices()
    assert markers._data is full_data
    assert markers.shared_program['a_position']._base is markers._full_vbo

    markers.set_view_indices(np.array([0, 2]))
    assert markers._view_vbo is view_vbo
    assert markers.shared_program['a_position']._base is view_vbo
    assert markers.shared_program['a_position'].size == 2


def test_points_viewport_culling_preserves_order_and_restores_full_view(
    monkeypatch,
):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    row, col = np.mgrid[:101, :101]
    data = 10 * np.column_stack((row.ravel(), col.ravel())).astype(float)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler
    culler._tree = cKDTree(data)
    culler._tree_generation = culler._generation
    full_data = visual.node.points_markers._full_data

    visual._prepare_viewbox(
        np.array([[0.0, 0.0], [1.0, 1.0]]), np.array([100, 100])
    )

    indices = culler._view_indices
    assert indices is not None
    assert len(indices) < len(data)
    assert np.all(indices[:-1] < indices[1:])
    np.testing.assert_array_equal(
        visual.node.points_markers._data, full_data[indices]
    )

    payload_bounds = culler._payload_bounds
    visual._prepare_viewbox(
        np.array([[0.1, 0.1], [1.1, 1.1]]), np.array([100, 100])
    )
    assert culler._view_indices is indices
    assert culler._payload_bounds is payload_bounds

    visual._prepare_viewbox(
        np.array([[0.75, 0.75], [1.75, 1.75]]), np.array([100, 100])
    )
    assert culler._view_indices is not indices

    visual._prepare_viewbox(
        np.array([[0.0, 0.0], [1000.0, 1000.0]]), np.array([100, 100])
    )
    assert (
        visual.node.points_markers._vbo is visual.node.points_markers._full_vbo
    )
    visual._prepare_viewbox(
        np.array([[0.0, 0.0], [1.0, 1.0]]), np.array([100, 100])
    )
    assert (
        visual.node.points_markers._vbo is visual.node.points_markers._view_vbo
    )

    layer.mode = Mode.SELECT
    assert culler._view_indices is None
    assert visual.node.points_markers._data is full_data


@pytest.mark.parametrize(
    ('area_ratio', 'uses_subset'),
    [(0.00149, True), (0.0015, True), (0.00151, False), (1.0, False)],
)
def test_points_viewport_area_boundary(monkeypatch, area_ratio, uses_subset):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    row, col = np.mgrid[:101, :101]
    data = 10 * np.column_stack((row.ravel(), col.ravel())).astype(float)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler
    culler._tree = cKDTree(data)
    culler._tree_generation = culler._generation
    width = np.sqrt(area_ratio) * 1000

    visual._prepare_viewbox(
        np.array([[0.0, 0.0], [width, width]]), np.array([100, 100])
    )

    assert (culler._view_indices is not None) is uses_subset


def test_points_viewport_culling_falls_back_for_visible_text_and_rotation(
    monkeypatch,
):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    data = np.linspace(0, 100, 400).reshape(200, 2)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler
    culler._tree = cKDTree(data)
    culler._tree_generation = culler._generation
    corners = np.array([[0.0, 0.0], [0.1, 0.1]])

    layer.text = {'string': {'constant': 'point'}}
    visual._prepare_viewbox(corners, np.array([100, 100]))
    assert culler._view_indices is None

    layer.text.visible = False
    layer.rotate = 30
    visual._prepare_viewbox(corners, np.array([100, 100]))
    assert culler._view_indices is None


def test_points_viewport_padding_includes_canvas_clamp_and_antialiasing():
    layer = Points(
        np.array([[0.0, 0.0], [100.0, 100.0]]),
        size=0.01,
        border_width=0,
        canvas_size_limits=(2, 10000),
    )
    visual = VispyPointsLayer(layer, font_info=FontInfo())

    _, _, padding = visual._viewport_culler._data_view_bounds(
        np.array([[0.0, 0.0], [100.0, 100.0]]), np.array([100, 100])
    )

    # The shader's 2 px body clamp adds a 1 px edge floor and 3 px of
    # antialiasing on each side, for a 6 px radius.
    np.testing.assert_allclose(padding, [6, 6])


def test_points_coordinate_change_invalidates_viewport_index(monkeypatch):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    data = np.linspace(0, 100, 400).reshape(200, 2)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler
    culler._tree = cKDTree(data)
    culler._tree_generation = culler._generation
    generation = culler._generation

    layer.data = data + 1

    assert culler._generation == generation + 1
    assert culler._tree is None
    assert culler._view_indices is None


def test_points_style_change_retains_viewport_index(monkeypatch):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    data = np.linspace(0, 100, 400).reshape(200, 2)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler
    tree = cKDTree(data)
    culler._tree = tree
    culler._tree_generation = culler._generation
    generation = culler._generation

    layer.size = 0.2

    assert culler._generation == generation
    assert culler._tree is tree


def test_points_viewport_index_builds_in_background(monkeypatch, qtbot):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    data = np.linspace(0, 1000, 4000).reshape(2000, 2)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler
    corners = np.array([[0.0, 0.0], [1.0, 1.0]])
    canvas_size = np.array([100, 100])

    visual._prepare_viewbox(corners, canvas_size)
    visual._prepare_viewbox(corners, canvas_size)

    assert culler._worker is not None
    qtbot.waitUntil(lambda: culler._tree is not None, timeout=5000)
    assert culler._tree_generation == culler._generation
    qtbot.waitUntil(lambda: culler._worker is None, timeout=5000)
    visual.close()


def test_points_viewport_index_is_not_built_for_full_view(monkeypatch):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    data = np.linspace(0, 1000, 4000).reshape(2000, 2)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    corners = np.array([[0.0, 0.0], [1000.0, 1000.0]])
    canvas_size = np.array([100, 100])

    visual._prepare_viewbox(corners, canvas_size)
    visual._prepare_viewbox(corners, canvas_size)

    assert visual._viewport_culler._worker is None


def test_points_failed_viewport_index_is_not_retried(monkeypatch):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    data = np.linspace(0, 1000, 4000).reshape(2000, 2)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler
    culler._on_index_ready((culler._generation, None))
    starts = []
    monkeypatch.setattr(culler, '_start_index_build', lambda: starts.append(1))

    corners = np.array([[0.0, 0.0], [1.0, 1.0]])
    visual._prepare_viewbox(corners, np.array([100, 100]))
    visual._prepare_viewbox(corners, np.array([100, 100]))

    assert starts == []


def test_points_payload_rejection_is_cached(monkeypatch):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    rng = np.random.default_rng(0)
    cluster = rng.uniform(0, 1, (100, 2))
    data = np.concatenate((cluster, [[1000, 1000], [0, 1000]]))
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler

    class CountingTree:
        def __init__(self, values):
            self._tree = cKDTree(values)
            self.data = self._tree.data
            self.mins = self._tree.mins
            self.maxes = self._tree.maxes
            self.calls = 0

        def query_ball_point(self, *args, **kwargs):
            self.calls += 1
            return self._tree.query_ball_point(*args, **kwargs)

    tree = CountingTree(data)
    culler._tree = tree
    culler._tree_generation = culler._generation
    corners = np.array([[0.0, 0.0], [0.5, 0.5]])

    visual._prepare_viewbox(corners, np.array([100, 100]))
    visual._prepare_viewbox(corners, np.array([100, 100]))

    assert culler._view_indices is None
    assert tree.calls == 1


def test_points_viewport_close_does_not_wait_for_index_build(
    monkeypatch, qtbot
):
    monkeypatch.setattr(_points_viewport_culling, 'MIN_POINTS', 100)
    started = Event()
    release = Event()

    def slow_build(data, generation):
        started.set()
        release.wait(timeout=5)
        return generation, None

    monkeypatch.setattr(_points_viewport_culling, '_build_index', slow_build)
    data = np.linspace(0, 1000, 4000).reshape(2000, 2)
    layer = Points(data, size=0.1)
    visual = VispyPointsLayer(layer, font_info=FontInfo())
    culler = visual._viewport_culler
    corners = np.array([[0.0, 0.0], [1.0, 1.0]])
    visual._prepare_viewbox(corners, np.array([100, 100]))
    visual._prepare_viewbox(corners, np.array([100, 100]))
    qtbot.waitUntil(started.is_set, timeout=5000)

    before = time.perf_counter()
    visual.close()
    elapsed = time.perf_counter() - before
    release.set()

    assert elapsed < 0.1
    qtbot.waitUntil(lambda: culler._worker is None, timeout=5000)
