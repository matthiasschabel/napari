"""GPU rendering of NaN and the infinities.

vispy's stock float pipeline clamps into the clim range, which makes an
infinity indistinguishable from a saturated finite value, and detects NaN with
a test that a fast-math compiler folds away. These tests render through
napari's actual image visual and read the pixels back.
"""

import numpy as np
import pytest
from vispy.scene import PanZoomCamera, SceneCanvas

from napari._vispy.visuals.image import (
    _APPLY_CLIM_FLOAT,
    Image as ImageNode,
)
from napari.utils.colormaps import Colormap
from napari.utils.colormaps.colormap_utils import _napari_cmap_to_vispy

VALUES = [-np.inf, -1.0, 0.0, 0.5, 1.0, 2.0, np.inf, np.nan]
NAMES = ['neg_inf', 'under', 'zero', 'half', 'one', 'over', 'pos_inf', 'nan']

BLACK_WHITE = [[0, 0, 0, 1], [1, 1, 1, 1]]
RED = [1, 0, 0, 1]
GREEN = [0, 1, 0, 1]
BLUE = [0, 0, 1, 1]
YELLOW = [1, 1, 0, 1]
CYAN = [0, 1, 1, 1]

_BACKGROUND = 'magenta'  # nothing in the colormaps under test produces it


def render_values(colormap: Colormap, clim=(0.0, 1.0)) -> dict:
    """Render one texel per probe value and return its RGB, keyed by name."""
    data = np.tile(np.array(VALUES, dtype=np.float32), (8, 1))
    canvas = SceneCanvas(size=(64, 64), show=False, bgcolor=_BACKGROUND)
    try:
        view = canvas.central_widget.add_view()
        ImageNode(
            data,
            cmap=_napari_cmap_to_vispy(colormap),
            clim=clim,
            interpolation='nearest',
            texture_format='auto',
            parent=view.scene,
        )
        view.camera = PanZoomCamera(aspect=1)
        view.camera.set_range(x=(0, len(VALUES)), y=(0, 8), margin=0)
        img = canvas.render(alpha=True)
    finally:
        canvas.close()

    # Locate the drawn quad rather than assuming it fills the canvas: the
    # device pixel ratio and the camera's framing are both platform-dependent.
    drawn = ~np.all(
        img == np.array([255, 0, 255, 255], dtype=np.uint8), axis=-1
    )
    ys, xs = np.where(drawn)
    assert xs.size, 'nothing was drawn'
    x0, x1 = xs.min(), xs.max() + 1
    row = (ys.min() + ys.max()) // 2
    return {
        name: tuple(
            int(c)
            for c in img[row, int(x0 + (i + 0.5) * (x1 - x0) / len(VALUES))][
                :3
            ]
        )
        for i, name in enumerate(NAMES)
    }


def as_rgb(color) -> tuple:
    return tuple(round(c * 255) for c in color[:3])


@pytest.mark.usefixtures('qapp')
def test_every_exceptional_class_gets_its_own_color():
    """The infinities must not collapse onto the saturation colors."""
    rendered = render_values(
        Colormap(
            BLACK_WHITE,
            name='testing',
            nan_color=RED,
            pos_inf_color=GREEN,
            neg_inf_color=BLUE,
            high_color=YELLOW,
            low_color=CYAN,
        )
    )
    assert rendered['neg_inf'] == as_rgb(BLUE)
    assert rendered['pos_inf'] == as_rgb(GREEN)
    assert rendered['nan'] == as_rgb(RED)
    # finite out-of-range values keep the saturation colors, unchanged
    assert rendered['under'] == as_rgb(CYAN)
    assert rendered['over'] == as_rgb(YELLOW)
    assert rendered['half'] == pytest.approx((128, 128, 128), abs=1)


@pytest.mark.usefixtures('qapp')
def test_nan_uses_nan_color_not_the_bottom_of_the_ramp():
    """Regression: NaN rendered as low_color wherever fast math is enabled.

    Apple's GL-on-Metal folds vispy's `!(d <= 0.0 || 0.0 <= d)` to false, so
    NaN fell through to clamp() and came out at the bottom of the colormap.
    """
    rendered = render_values(
        Colormap(BLACK_WHITE, name='testing', nan_color=RED, low_color=CYAN)
    )
    assert rendered['nan'] == as_rgb(RED)
    assert rendered['nan'] != rendered['zero']


@pytest.mark.usefixtures('qapp')
def test_infinities_fall_back_to_high_and_low_colors():
    """With no infinity colors set, rendering is what it was before."""
    rendered = render_values(
        Colormap(
            BLACK_WHITE, name='testing', high_color=YELLOW, low_color=CYAN
        )
    )
    assert rendered['pos_inf'] == as_rgb(YELLOW) == rendered['over']
    assert rendered['neg_inf'] == as_rgb(CYAN) == rendered['under']


@pytest.mark.usefixtures('qapp')
def test_infinities_fall_back_to_the_ramp_ends():
    """With neither infinity nor saturation colors, the ramp ends are used."""
    rendered = render_values(Colormap(BLACK_WHITE, name='testing'))
    assert rendered['neg_inf'] == (0, 0, 0)
    assert rendered['pos_inf'] == (255, 255, 255)


def test_nan_test_uses_two_uniform_bounds():
    """Guard the property the NaN test depends on.

    A fast-math compiler assumes no NaN exists, which makes `d <= X || X <= d`
    provable for any single bound X and folds the test to false. Two bounds it
    cannot relate survive. Writing the bound as a literal, or comparing twice
    against the same one, silently reintroduces the bug on Apple Silicon while
    every CPU-side test keeps passing, so assert the shape here.
    """
    assert '$flt_max' in _APPLY_CLIM_FLOAT
    assert '!(data <= $flt_max) && !(data >= -$flt_max)' in _APPLY_CLIM_FLOAT
    # no literal float32 max anywhere in the classification
    assert '3.40282' not in _APPLY_CLIM_FLOAT


def test_colormap_prologue_resolves_fallbacks_on_the_cpu():
    """Fallback resolution belongs off the per-fragment path."""
    glsl = _napari_cmap_to_vispy(
        Colormap(BLACK_WHITE, name='testing', high_color=YELLOW)
    ).glsl_map
    # +inf falls back to high_color, -inf to the first ramp color
    assert 'vec4(1.000000, 1.000000, 0.000000, 1.000000)' in glsl
    assert 'vec4(0.000000, 0.000000, 0.000000, 1.000000)' in glsl
    # and the prologue precedes every check vispy injects
    assert glsl.index('exceptional-value classes') < glsl.index('bad_color')
