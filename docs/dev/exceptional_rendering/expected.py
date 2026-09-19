# ruff: noqa: INP001  # standalone dev script, not an importable package
"""CPU reference values for the GL exceptional-value probes.

Everything the GPU is asked about is answered here first, in numpy, so a probe
verdict is a comparison against a known quantity rather than a judgement call
made while reading driver output.
"""

from __future__ import annotations

import numpy as np

# Value classes. The codes travel in the red channel of an RGBA8 render target,
# so they must be small positive integers that survive the /255 round trip.
CLASS_NEG_INF = 1
CLASS_POS_INF = 2
CLASS_NAN = 3
CLASS_FINITE = 4

CLASS_NAMES = {
    CLASS_NEG_INF: 'neg_inf',
    CLASS_POS_INF: 'pos_inf',
    CLASS_NAN: 'nan',
    CLASS_FINITE: 'finite',
}

FLT_MAX = np.float32(3.4028234663852886e38)


def _nan_with_payload(payload: int) -> np.float32:
    """A quiet NaN carrying a distinguishable mantissa payload."""
    bits = np.uint32(0x7FC00000 | (payload & 0x003FFFFF))
    return bits.view(np.float32)


# The probe values, in texel order. Keep this ordering stable: result files
# index by name, but the shaders index by position.
VALUES: dict[str, np.float32] = {
    'neg_inf': np.float32(-np.inf),
    'pos_inf': np.float32(np.inf),
    'nan': np.float32(np.nan),
    'nan_payload': _nan_with_payload(0x2A2A2A),
    'denormal': np.float32(1e-40),  # subnormal in float32
    'flt_max': FLT_MAX,
    'neg_flt_max': -FLT_MAX,
    'neg_zero': np.float32(-0.0),
    'zero': np.float32(0.0),
    'half': np.float32(0.5),
    'one': np.float32(1.0),
    'two': np.float32(2.0),
    'neg_one': np.float32(-1.0),
}

NAMES = list(VALUES)


def data_array() -> np.ndarray:
    """The 1 x N float32 texture the probes upload."""
    return np.array([[VALUES[n] for n in NAMES]], dtype=np.float32)


def expected_class(name: str) -> int:
    v = VALUES[name]
    if np.isnan(v):
        return CLASS_NAN
    if np.isposinf(v):
        return CLASS_POS_INF
    if np.isneginf(v):
        return CLASS_NEG_INF
    return CLASS_FINITE


def expected_bits(name: str) -> int:
    """The float32 bit pattern, as an int, that an intact upload preserves."""
    return int(np.float32(VALUES[name]).view(np.uint32))


def expected_stock_gray(name: str, clim: tuple[float, float] = (0.0, 1.0)) -> int | None:
    """Gray level (0-255) vispy's current float pipeline should show.

    Replicates ``_APPLY_CLIM_FLOAT`` followed by the colormap's
    ``clamp(t, 0, 1)`` LUT lookup against a black-to-white ramp. Returns None
    for NaN, where the result is genuinely undefined by the GLSL spec: NaN is
    passed through apply_clim untouched and then handed to ``clamp``, whose
    behavior with a NaN argument the spec does not define.
    """
    v = float(VALUES[name])
    if np.isnan(v):
        return None
    lo, hi = clim
    v = min(max(v, min(lo, hi)), max(lo, hi))
    t = (v - lo) / (hi - lo)
    t = min(max(t, 0.0), 1.0)
    return round(t * 255)


# The subnormal question is separate from the class question: a driver running
# with flush-to-zero turns 1e-40 into 0.0 while still reporting it finite.
DENORMAL_BITS = expected_bits('denormal')


# --- proposed napari exceptional-value chain -------------------------------
#
# The design routes each class to its own color by returning a sentinel from
# apply_clim, passing it untouched through apply_gamma, and decoding it in the
# colormap function before any LUT lookup. Normal t is in [0, 1], so any value
# below -0.5 is unambiguously a sentinel.

NAN_SENTINEL = -1.0
POS_INF_SENTINEL = -2.0
NEG_INF_SENTINEL = -3.0

# Distinct 8-bit colors so a wrong route is obvious in the readback.
CHAIN_COLORS = {
    'nan': (255, 0, 0),
    'pos_inf': (0, 255, 0),
    'neg_inf': (0, 0, 255),
    'high': (255, 255, 0),
    'low': (0, 255, 255),
}


def expected_chain_color(name: str, clim: tuple[float, float] = (0.0, 1.0)):
    """Which color the proposed chain must produce, computed on the CPU.

    Mirrors vispy's existing epsilon semantics for low and high: a normalized
    t within 1e-12 of either end takes low_color or high_color rather than the
    ramp endpoint.
    """
    v = float(VALUES[name])
    if np.isnan(v):
        return CHAIN_COLORS['nan']
    if np.isposinf(v):
        return CHAIN_COLORS['pos_inf']
    if np.isneginf(v):
        return CHAIN_COLORS['neg_inf']
    lo, hi = clim
    clamped = min(max(v, min(lo, hi)), max(lo, hi))
    t = (clamped - lo) / (hi - lo)
    if t <= 1e-12:
        return CHAIN_COLORS['low']
    if 1 - t <= 1e-12:
        return CHAIN_COLORS['high']
    gray = int(round(t * 255))
    return (gray, gray, gray)
