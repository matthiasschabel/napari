from typing import ClassVar

import numpy as np
from vispy.scene.visuals import Image as BaseImage

from napari._vispy.visuals.util import TextureMixin
from napari.utils.colormaps.colormap_utils import (
    NAN_SENTINEL,
    NEG_INF_SENTINEL,
    POS_INF_SENTINEL,
    SENTINEL_CUTOFF,
)

# Largest finite float32. Bound as a uniform, not written as a literal, for the
# reason spelled out in _APPLY_CLIM_FLOAT below.
_FLT_MAX = float(np.finfo(np.float32).max)

_APPLY_CLIM_FLOAT = f"""
    float apply_clim(float data) {{
        // Classify before clamping. Clamping maps both infinities onto the
        // clim endpoints, which destroys the very distinction we need.
        if (!(data <= $flt_max) && !(data >= -$flt_max)) return {NAN_SENTINEL};
        if (data >  $flt_max) return {POS_INF_SENTINEL};
        if (data < -$flt_max) return {NEG_INF_SENTINEL};

        data = clamp(data, min($clim.x, $clim.y), max($clim.x, $clim.y));
        data = (data - $clim.x) / ($clim.y - $clim.x);
        return data;
    }}"""

_APPLY_GAMMA_FLOAT = f"""
    float apply_gamma(float data) {{
        // pow() of a negative base is NaN, so class sentinels bypass it.
        if (data < {SENTINEL_CUTOFF}) return data;
        return pow(data, $gamma);
    }}"""


# If data is not present, we need bounds to be None (see napari#3517)
class Image(TextureMixin, BaseImage):
    """napari's image visual, with exceptional values classified in the shader.

    vispy's stock float pipeline detects NaN with `!(d <= 0.0 || 0.0 <= d)` and
    otherwise clamps into the clim range. That has two consequences napari does
    not want. The infinities become indistinguishable from saturated finite
    values, and the NaN test does not survive a fast-math compiler: assuming no
    NaN exists, `d <= X || X <= d` is provable for any single bound X, so the
    test folds to false. Apple's GL-on-Metal enables fast math by default, and
    NaN there renders as the bottom of the colormap rather than nan_color.

    The test below uses two bounds the compiler cannot relate. Proving it false
    would require knowing `$flt_max >= -$flt_max`, and a uniform denies it
    that. The classes then travel to the colormap as out-of-band sentinel
    values, decoded by _ExceptionalVispyColormap's prologue.

    This covers the 2D image path only. The volume visual composes its own
    shaders and is untouched.
    """

    _func_templates: ClassVar[dict[str, str]] = {
        **BaseImage._func_templates,
        'clim_float': _APPLY_CLIM_FLOAT,
        'gamma_float': _APPLY_GAMMA_FLOAT,
    }

    def _build_color_transform(self):
        chain = super()._build_color_transform()
        # Bind by template variable rather than by position: vispy's chain
        # order is an implementation detail, the variable name is the contract.
        for func in chain.functions:
            if 'flt_max' in func.template_vars:
                func['flt_max'] = _FLT_MAX
        return chain

    def _compute_bounds(self, axis, view):
        if self._data is None:
            return None
        if axis > 1:
            return (0, 0)

        return (0, self.size[axis])
