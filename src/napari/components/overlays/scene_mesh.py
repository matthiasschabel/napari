import numpy as np
from pydantic import Field, field_validator

from napari.components.overlays.base import SceneOverlay
from napari.utils.color import ColorValue
from napari.utils.events.custom_types import Array


def _empty_triangles() -> np.ndarray:
    triangles = np.empty((0, 3, 0), dtype=np.float32)
    triangles.flags.writeable = False
    return triangles


class SceneMeshOverlay(SceneOverlay):
    """Batched triangles in full-dimensional world coordinates.

    ``triangles`` has shape ``(N, 3, D)``, where ``D`` must match the viewer
    dimensionality. The rendering backend projects the currently displayed
    dimensions and reverses them only at the Vispy boundary.
    """

    triangles: Array[np.float32] = Field(default_factory=_empty_triangles)
    color: ColorValue = Field(default_factory=lambda: ColorValue('white'))

    @field_validator('triangles')
    @classmethod
    def _validate_triangles(cls, value: np.ndarray) -> np.ndarray:
        if value.ndim != 3 or value.shape[1] != 3:
            raise ValueError('triangles must have shape (N, 3, D)')
        if value.shape[0] and value.shape[2] < 2:
            raise ValueError('triangles must have at least two coordinates')
        if not np.all(np.isfinite(value)):
            raise ValueError('triangles must contain only finite values')
        triangles = np.array(value, dtype=np.float32, order='C', copy=True)
        triangles.flags.writeable = False
        return triangles
