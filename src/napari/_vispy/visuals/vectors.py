from __future__ import annotations

from typing import TYPE_CHECKING

from vispy.scene.visuals import Compound

from napari._vispy.visuals.clipping_planes_mixin import ClippingPlanesMixin
from napari._vispy.visuals.mesh import Mesh

if TYPE_CHECKING:
    from napari._vispy.utils.qt_font import FontInfo


class VectorsVisual(ClippingPlanesMixin, Compound):
    """
    Vectors vispy visual with clipping plane functionality
    """

    def __init__(self, font_info: FontInfo) -> None:
        super().__init__([Mesh()], font_info=font_info)

    @property
    def mesh(self) -> Mesh:
        """Mesh containing the vectors."""
        return self._subvisuals[0]
