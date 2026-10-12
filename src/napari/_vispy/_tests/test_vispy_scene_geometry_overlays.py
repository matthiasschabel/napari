import numpy as np
import pytest

from napari._vispy.overlays.scene_line import VispySceneLineOverlay
from napari._vispy.overlays.scene_mesh import VispySceneMeshOverlay
from napari._vispy.utils.qt_font import FontInfo
from napari._vispy.utils.visual import create_vispy_overlay
from napari.components import ViewerModel
from napari.components.overlays import SceneLineOverlay, SceneMeshOverlay


def test_scene_line_overlay_projects_displayed_world_dimensions() -> None:
    viewer = ViewerModel()
    viewer.dims.ndim = 4
    viewer.dims.order = (3, 0, 2, 1)
    overlay = SceneLineOverlay(
        segments=np.array([[[10, 20, 30, 40], [11, 21, 31, 41]]]),
        visible=True,
        color='yellow',
        width=2,
    )
    visual = VispySceneLineOverlay(
        viewer=viewer, overlay=overlay, font_info=FontInfo()
    )

    np.testing.assert_array_equal(
        visual.node._pos, np.array([[20, 30], [21, 31]], dtype=np.float32)
    )
    assert visual.node.visible is True

    viewer.dims.order = (3, 2, 1, 0)
    np.testing.assert_array_equal(
        visual.node._pos, np.array([[10, 20], [11, 21]], dtype=np.float32)
    )

    viewer.dims.ndisplay = 3
    np.testing.assert_array_equal(
        visual.node._pos,
        np.array([[10, 20, 30], [11, 21, 31]], dtype=np.float32),
    )

    overlay.segments = np.empty((0, 2, 4))
    assert visual.node.visible is False
    visual.close()


def test_scene_line_overlay_updates_style_and_disconnects_on_close() -> None:
    viewer = ViewerModel()
    overlay = SceneLineOverlay(
        segments=np.array([[[0, 0], [1, 1]]]), visible=True
    )
    visual = VispySceneLineOverlay(
        viewer=viewer, overlay=overlay, font_info=FontInfo()
    )

    overlay.color = 'yellow'
    overlay.width = 3
    assert visual.node._width == 3
    np.testing.assert_array_equal(visual.node._color, overlay.color)

    positions = visual.node._pos.copy()
    visual.close()
    overlay.segments = np.array([[[2, 2], [3, 3]]])
    viewer.dims.order = viewer.dims.order[::-1]
    np.testing.assert_array_equal(visual.node._pos, positions)


def test_scene_mesh_overlay_projects_displayed_world_dimensions() -> None:
    viewer = ViewerModel()
    viewer.dims.ndim = 3
    overlay = SceneMeshOverlay(
        triangles=np.array([[[1, 2, 3], [4, 5, 6], [7, 8, 9]]], dtype=float),
        visible=True,
        color='#00FFFF40',
    )
    visual = VispySceneMeshOverlay(
        viewer=viewer, overlay=overlay, font_info=FontInfo()
    )

    np.testing.assert_array_equal(
        visual.node.mesh_data.get_vertices(),
        np.array([[3, 2], [6, 5], [9, 8]], dtype=np.float32),
    )
    np.testing.assert_array_equal(
        visual.node.mesh_data.get_faces(), np.array([[0, 1, 2]])
    )
    assert visual.node.visible is True

    viewer.dims.ndisplay = 3
    np.testing.assert_array_equal(
        visual.node.mesh_data.get_vertices(),
        np.array([[3, 2, 1], [6, 5, 4], [9, 8, 7]], dtype=np.float32),
    )

    overlay.triangles = np.empty((0, 3, 3))
    assert visual.node.visible is False
    visual.close()


@pytest.mark.parametrize(
    ('overlay', 'visual_type'),
    [
        (
            SceneLineOverlay(segments=np.zeros((1, 2, 3)), visible=True),
            VispySceneLineOverlay,
        ),
        (
            SceneMeshOverlay(triangles=np.zeros((1, 3, 3)), visible=True),
            VispySceneMeshOverlay,
        ),
    ],
)
def test_scene_geometry_overlay_requires_full_viewer_dimensionality(
    overlay, visual_type
) -> None:
    viewer = ViewerModel()

    with pytest.raises(ValueError, match='dimensionality must match'):
        visual_type(viewer=viewer, overlay=overlay, font_info=FontInfo())


@pytest.mark.parametrize(
    ('overlay', 'visual_type'),
    [
        (SceneLineOverlay(visible=True), VispySceneLineOverlay),
        (SceneMeshOverlay(visible=True), VispySceneMeshOverlay),
    ],
)
def test_scene_geometry_overlay_resolves_registered_visual(
    overlay, visual_type
) -> None:
    viewer = ViewerModel()
    visual = create_vispy_overlay(overlay, viewer=viewer, font_info=FontInfo())

    assert isinstance(visual, visual_type)
    visual.close()


@pytest.mark.parametrize(
    ('overlay', 'visual_type'),
    [
        (
            SceneLineOverlay(segments=np.zeros((1, 2, 3)), visible=True),
            VispySceneLineOverlay,
        ),
        (
            SceneMeshOverlay(triangles=np.zeros((1, 3, 3)), visible=True),
            VispySceneMeshOverlay,
        ),
    ],
)
def test_scene_geometry_overlay_hides_when_viewer_ndim_changes(
    overlay, visual_type
) -> None:
    viewer = ViewerModel()
    viewer.add_image(np.zeros((4, 5, 6)))
    visual = visual_type(viewer=viewer, overlay=overlay, font_info=FontInfo())

    viewer.layers.pop()

    assert visual.node.visible is False

    viewer.add_image(np.zeros((4, 5, 6)))
    assert visual.node.visible is True
    visual.close()
