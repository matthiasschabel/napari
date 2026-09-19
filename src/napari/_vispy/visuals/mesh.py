from __future__ import annotations

from typing import TYPE_CHECKING

from vispy.scene.visuals import Mesh as VispyMesh
from vispy.visuals.shaders import Function

if TYPE_CHECKING:
    import numpy as np


class Mesh(VispyMesh):
    """Mesh that reuses the shader function for non-scalar colors.

    VisPy creates an equivalent no-op Function on every data update. Reusing
    one immutable instance makes shader assignment an identity-preserving
    no-op instead of marking every dependent view program for recompilation.
    """

    _null_color_transform = Function('vec4 pass(vec4 color) { return color; }')

    def _build_color_transform(self, colors: np.ndarray) -> Function:
        if colors.ndim == 2 and colors.shape[1] == 1:
            return super()._build_color_transform(colors)
        return self._null_color_transform
