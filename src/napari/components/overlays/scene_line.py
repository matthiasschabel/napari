import numpy as np
from pydantic import Field, field_validator

from napari.components.overlays.base import SceneOverlay
from napari.utils.color import ColorValue
from napari.utils.events.custom_types import Array


def _empty_segments() -> np.ndarray:
    segments = np.empty((0, 2, 0), dtype=np.float32)
    segments.flags.writeable = False
    return segments


class SceneLineOverlay(SceneOverlay):
    """Batched line segments in full-dimensional world coordinates.

    ``segments`` has shape ``(N, 2, D)``, where ``D`` must match the viewer
    dimensionality. The rendering backend projects the currently displayed
    dimensions and reverses them only at the Vispy boundary.
    """

    segments: Array[np.float32] = Field(default_factory=_empty_segments)
    color: ColorValue = Field(default_factory=lambda: ColorValue('white'))
    width: float = Field(default=1.0, gt=0)

    @field_validator('segments')
    @classmethod
    def _validate_segments(cls, value: np.ndarray) -> np.ndarray:
        if value.ndim != 3 or value.shape[1] != 2:
            raise ValueError('segments must have shape (N, 2, D)')
        if value.shape[0] and value.shape[2] < 2:
            raise ValueError('segments must have at least two coordinates')
        if not np.all(np.isfinite(value)):
            raise ValueError('segments must contain only finite values')
        segments = np.array(value, dtype=np.float32, order='C', copy=True)
        segments.flags.writeable = False
        return segments
