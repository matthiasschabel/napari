"""GPU rendering of NaN and the infinities.

vispy's stock float pipeline clamps into the clim range, which makes an
infinity indistinguishable from a saturated finite value, and detects NaN with
a test that a fast-math compiler folds away. These tests render through
napari's actual image visual and read the pixels back.
"""

import numpy as np
import pytest
from vispy.scene import PanZoomCamera, SceneCanvas

from napari._vispy.layers.tiled_image import TiledImageNode
from napari._vispy.visuals.image import (
    _APPLY_CLIM_FLOAT,
    Image as ImageNode,
)
from napari.utils.colormaps import Colormap
from napari.utils.colormaps.colormap_utils import _napari_cmap_to_vispy

# A guard texel at each end. nan_color is transparent by default, and a
# transparent fragment is indistinguishable from the canvas background, so
# without guards a trailing NaN shrinks the detected region and shifts every
# sample by a fraction of a texel.
GUARD = 0.5
VALUES = [GUARD, -np.inf, -1.0, 0.0, 0.5, 1.0, 2.0, np.inf, np.nan, GUARD]
NAMES = [
    '_guard_lo',
    'neg_inf',
    'under',
    'zero',
    'half',
    'one',
    'over',
    'pos_inf',
    'nan',
    '_guard_hi',
]

BLACK_WHITE = [[0, 0, 0, 1], [1, 1, 1, 1]]
RED = [1, 0, 0, 1]
GREEN = [0, 1, 0, 1]
BLUE = [0, 0, 1, 1]
YELLOW = [1, 1, 0, 1]
CYAN = [0, 1, 1, 1]

_BACKGROUND = 'magenta'  # nothing in the colormaps under test produces it


def _plain_node(data, view, vispy_cmap, clim):
    return ImageNode(
        data,
        cmap=vispy_cmap,
        clim=clim,
        interpolation='nearest',
        texture_format='auto',
        parent=view.scene,
    )


def _tiled_node(data, view, vispy_cmap, clim):
    """The path taken when an image exceeds the GPU's texture size limit."""
    node = TiledImageNode(data, tile_size=4, texture_format='auto')
    node.parent = view.scene
    node.cmap = vispy_cmap
    node.clim = clim
    node.interpolation = 'nearest'
    return node


def _retiled_node(data, view, vispy_cmap, clim):
    """Configure the node, then force a tile-count change.

    Children are replaced wholesale when the tile count changes, so this is
    the path where previously assigned state is lost if it is not carried
    over. Configuring before the change is the whole point.
    """
    node = TiledImageNode(data, tile_size=len(VALUES), texture_format='auto')
    node.parent = view.scene
    node.cmap = vispy_cmap
    node.clim = clim
    node.interpolation = 'nearest'
    node.tile_size = 4
    node.set_data(data)  # different tile count: every child is rebuilt
    return node


def render_values(
    colormap: Colormap, clim=(0.0, 1.0), node=_plain_node, gamma=None
) -> dict:
    """Render one texel per probe value and return its RGB, keyed by name."""
    data = np.tile(np.array(VALUES, dtype=np.float32), (8, 1))
    canvas = SceneCanvas(size=(64, 64), show=False, bgcolor=_BACKGROUND)
    try:
        view = canvas.central_widget.add_view()
        created = node(
            data,
            view,
            _napari_cmap_to_vispy(colormap, decode_sentinels=True),
            clim,
        )
        if gamma is not None:
            created.gamma = gamma
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


@pytest.mark.usefixtures('qapp')
def test_tiled_rendering_matches_untiled():
    """An image must not change appearance when it crosses the texture limit.

    Images too large for one texture are split across child nodes by
    TiledImageNode. Those children built vispy's Image rather than napari's,
    so they kept the stock shader: NaN came out as low_color and +inf as
    high_color on exactly the same data that rendered correctly untiled.
    """
    cmap = Colormap(
        BLACK_WHITE,
        name='testing',
        nan_color=RED,
        pos_inf_color=GREEN,
        neg_inf_color=BLUE,
        high_color=YELLOW,
        low_color=CYAN,
    )
    untiled = render_values(cmap, node=_plain_node)
    tiled = render_values(cmap, node=_tiled_node)
    assert tiled == untiled
    # and the classes are actually distinct, so the comparison is not vacuous
    assert tiled['nan'] == as_rgb(RED)
    assert tiled['pos_inf'] == as_rgb(GREEN)

    # A data update that changes the tile count rebuilds every child. The
    # colormap, clim and gamma assigned before that must survive it, or the
    # tiles come back on vispy's defaults and the image changes appearance on
    # a reshape.
    retiled = render_values(cmap, node=_retiled_node)
    assert retiled == untiled


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


def test_sentinel_decoding_is_off_by_default():
    """Only a visual that emits sentinels may decode them.

    vispy's mesh visual feeds the colormap an unclamped
    (val - cmin) / (cmax - cmin), so a surface vertex below the contrast
    limits arrives as a large negative t. If the prologue were unconditional,
    that legitimate under-range value would be painted with nan_color or an
    infinity color, on a default colormap as much as a configured one.
    """
    plain = _napari_cmap_to_vispy(Colormap(BLACK_WHITE, name='testing'))
    assert 'exceptional-value classes' not in plain.glsl_map
    opted_in = _napari_cmap_to_vispy(
        Colormap(BLACK_WHITE, name='testing'), decode_sentinels=True
    )
    assert 'exceptional-value classes' in opted_in.glsl_map


@pytest.mark.usefixtures('qapp')
def test_surface_colormap_leaves_under_range_values_alone():
    """Regression: the surface path must not decode sentinels."""
    from napari._vispy.layers.surface import VispySurfaceLayer
    from napari._vispy.utils.qt_font import FontInfo
    from napari.layers import Surface

    vertices = np.array([[0, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
    faces = np.array([[0, 1, 2]])
    # a vertex value well below the contrast limits: normalized t = -2.0,
    # which is the +inf sentinel
    values = np.array([-2.0, 0.5, 1.0], dtype=np.float32)
    layer = Surface((vertices, faces, values))
    layer.contrast_limits = (0.0, 1.0)
    visual = VispySurfaceLayer(layer, font_info=FontInfo())
    assert 'exceptional-value classes' not in visual.node.cmap.glsl_map


@pytest.mark.usefixtures('qapp')
@pytest.mark.parametrize('gamma', [0.5, 2.2])
def test_class_colors_survive_gamma(gamma):
    """pow() of a negative base is NaN, so sentinels must bypass apply_gamma.

    Without the bypass every class would come back as whatever the driver
    makes of pow(negative, gamma), and the failure would only appear for
    users who moved the gamma slider.
    """
    cmap = Colormap(
        BLACK_WHITE,
        name='testing',
        nan_color=RED,
        pos_inf_color=GREEN,
        neg_inf_color=BLUE,
    )
    plain = render_values(cmap)
    adjusted = render_values(cmap, gamma=gamma)

    for name in ('neg_inf', 'pos_inf', 'nan'):
        assert adjusted[name] == plain[name], f'{name} moved with gamma'
    # and gamma still applies to ordinary data, so this is not passing because
    # the whole stage was skipped
    assert adjusted['half'] != plain['half']


@pytest.mark.usefixtures('qapp')
@pytest.mark.parametrize(
    'kwargs',
    [
        {},
        {'high_color': YELLOW, 'low_color': CYAN},
        {'pos_inf_color': GREEN, 'neg_inf_color': BLUE, 'nan_color': RED},
    ],
    ids=['bare', 'saturation-only', 'infinity-colors'],
)
def test_gpu_agrees_with_cpu_on_every_class(kwargs):
    """The shader and Colormap.map must not disagree about a class.

    They are what a user compares without knowing it: the canvas is rendered
    on the GPU while the layer thumbnail and any CPU fallback go through
    map(). This renders each class and checks it against the CPU answer for
    the same value, so a divergence in the fallback order shows up here
    rather than as a thumbnail that does not match the canvas.
    """
    cmap = Colormap(BLACK_WHITE, name='testing', **kwargs)
    rendered = render_values(cmap)
    expected = cmap.map(np.array([np.nan, np.inf, -np.inf]))

    background = np.array([255, 0, 255], dtype=float)  # _BACKGROUND, magenta
    for name, cpu in zip(('nan', 'pos_inf', 'neg_inf'), expected, strict=True):
        # The canvas composites over its background, and nan_color is
        # transparent by default, so the CPU answer has to be composited the
        # same way before the two are comparable.
        alpha = float(cpu[3])
        composited = np.asarray(
            cpu[:3], dtype=float
        ) * 255 * alpha + background * (1 - alpha)
        assert rendered[name] == pytest.approx(composited, abs=1), (
            f'{name}: GPU {rendered[name]} disagrees with CPU {cpu}'
        )


@pytest.mark.usefixtures('qapp')
def test_retiling_preserves_every_pass_through_attribute():
    """Each forwarded attribute must survive a tile-count change.

    The render comparison only exercises the ones that change pixels, and
    `opacity` is the awkward case: it is forwarded to the children like the
    rest, but it is also a real VisualNode property, so reading it off the
    node returns the node's untouched default rather than what was assigned.
    """
    from napari._vispy.layers.tiled_image import (
        PASS_THROUGH_ATTRIBUTES,
        TiledImageNode,
    )

    data = np.zeros((8, 8), dtype=np.float32)
    node = TiledImageNode(data, tile_size=4, texture_format='auto')
    node.cmap = _napari_cmap_to_vispy(
        Colormap(BLACK_WHITE, name='testing'), decode_sentinels=True
    )
    node.clim = (0.0, 1.0)
    node.gamma = 2.0
    node.opacity = 0.5
    node.interpolation = 'nearest'

    before = {
        name: getattr(node.adopted_children[0], name)
        for name in PASS_THROUGH_ATTRIBUTES
    }
    node.tile_size = 2
    node.set_data(data)  # different tile count: every child is rebuilt
    after = {
        name: getattr(node.adopted_children[0], name)
        for name in PASS_THROUGH_ATTRIBUTES
    }

    assert len(node.adopted_children) == 16
    for name in PASS_THROUGH_ATTRIBUTES:
        if isinstance(before[name], np.ndarray):
            np.testing.assert_array_equal(
                after[name], before[name], err_msg=f'{name} not carried over'
            )
        else:
            assert after[name] == before[name], f'{name} not carried over'
