from typing import ClassVar

import numpy as np

from napari.components._viewer_constants import CursorStyle
from napari.utils.events import EventedModel


class Cursor(EventedModel):
    """Cursor object with position and properties of the cursor.

    ``OPERATION_CURSOR_VERSION`` is a class-level capability marker. Version 1
    accepts and renders the ``add`` and ``remove`` styles.

    Attributes
    ----------
    position : tuple of float
        Position of the cursor in world coordinates. If the cursor is outside of,
        the canvas, then the last known position is stored instead.
    viewbox : tuple[int, int] or None
        Position of the cursor in the grid.
    scaled : bool
        Flag to indicate whether cursor size should be scaled to zoom.
        Only relevant for circle and square cursors which are drawn
        with a particular size.
    size : float
        Size of the cursor in canvas pixels. Only relevant for circle
        and square cursors which are drawn with a particular size.
    style : str
        Style of the cursor. Must be one of
            * square: A square
            * circle: A circle
            * cross: A cross
            * forbidden: A forbidden symbol
            * pointing: A finger for pointing
            * standard: The standard cursor
            # crosshair: A crosshair
            * add: A pointer indicating an additive operation
            * remove: A pointer indicating a subtractive operation
    _view_direction : Optional[np.ndarray]
        The vector describing the direction of the camera in the scene
        in world coordinates.
        This is None when viewing in 2D.
    """

    OPERATION_CURSOR_VERSION: ClassVar[int] = 1

    # fields
    position: tuple[float, ...] = (1.0, 1.0)
    viewbox: tuple[int, int] | None = None
    scaled: bool = True
    size: float = 1.0
    style: CursorStyle = CursorStyle.STANDARD
    _view_direction: np.ndarray | None = None
