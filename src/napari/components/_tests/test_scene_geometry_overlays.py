import numpy as np
import pytest
from pydantic import ValidationError

from napari.components.overlays import SceneLineOverlay, SceneMeshOverlay


@pytest.mark.parametrize(
    ('overlay_type', 'field', 'geometry'),
    [
        (SceneLineOverlay, 'segments', np.arange(12).reshape(2, 2, 3)),
        (SceneMeshOverlay, 'triangles', np.arange(18).reshape(2, 3, 3)),
    ],
)
def test_scene_geometry_overlay_owns_immutable_float32_geometry(
    overlay_type, field, geometry
) -> None:
    overlay = overlay_type(**{field: geometry})
    stored = getattr(overlay, field)

    assert stored.dtype == np.float32
    assert stored.flags.c_contiguous
    assert stored.flags.writeable is False
    assert not np.shares_memory(stored, geometry)
    assert geometry.flags.writeable is True


@pytest.mark.parametrize(
    ('overlay_type', 'field', 'geometry', 'message'),
    [
        (
            SceneLineOverlay,
            'segments',
            np.zeros((2, 3, 2)),
            'segments must have shape',
        ),
        (
            SceneLineOverlay,
            'segments',
            np.array([[[0.0, np.nan], [1.0, 1.0]]]),
            'segments must contain only finite values',
        ),
        (
            SceneMeshOverlay,
            'triangles',
            np.zeros((2, 2, 2)),
            'triangles must have shape',
        ),
        (
            SceneMeshOverlay,
            'triangles',
            np.array([[[0.0, 0.0], [1.0, np.inf], [1.0, 1.0]]]),
            'triangles must contain only finite values',
        ),
    ],
)
def test_scene_geometry_overlay_rejects_invalid_geometry(
    overlay_type, field, geometry, message
) -> None:
    with pytest.raises(ValidationError, match=message):
        overlay_type(**{field: geometry})
