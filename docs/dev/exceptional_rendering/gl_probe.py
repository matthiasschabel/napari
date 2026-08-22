#!/usr/bin/env python
# ruff: noqa: T201  # standalone dev script; printing is its job
"""Probe what this GPU and driver actually do with infinities, NaN, and subnormals.

Stage 4a of the plan in
``cmap/docs/dev/mcslab/napari_exceptional_rendering_plan.md``: answer, on real
hardware, the questions that decide whether napari can classify exceptional
values in the shader (Option A) or has to sanitize data on the CPU and carry a
sidecar class texture (Option B).

Each probe suite runs in its own subprocess. Contexts are per-process, and a
driver that dies on a shader has to take only its own probe down with it.

    python gl_probe.py                # run everything, write results/<slug>.json
    python gl_probe.py --probe env    # run one suite, print its JSON
    python gl_probe.py --self-test    # prove the harness can report a failure
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable

sys.path.insert(0, str(Path(__file__).parent))
import expected as exp

HERE = Path(__file__).parent
RESULTS = HERE / 'results'
SENTINEL = '@@PROBE_RESULT@@'

# Verdict vocabulary. `unavailable` is a real answer, not a failure to answer:
# it means the platform cannot run the probe, which is itself evidence.
PASS, FAIL, UNAVAILABLE, ERROR = 'pass', 'fail', 'unavailable', 'error'

VERT = """
attribute vec2 a_pos;
varying vec2 v_tex;
void main() {
    v_tex = (a_pos + 1.0) * 0.5;
    gl_Position = vec4(a_pos, 0.0, 1.0);
}
"""

# The literal below is FLT_MAX exactly, so `v > FLT_MAX_LIT` is true only for
# +inf on a conforming compiler.
FLT_MAX_LIT = '3.402823466e+38'

_PROBES: dict[str, Callable[[], dict[str, Any]]] = {}


def probe(name: str) -> Callable:
    def wrap(fn):
        _PROBES[name] = fn
        return fn

    return wrap


# --------------------------------------------------------------------------
# GL plumbing


def _canvas(width: int, config: dict | None = None):
    """A hidden canvas in the same default configuration napari uses.

    napari builds a plain vispy SceneCanvas and requests no particular GL
    version or profile, so an unconfigured Canvas is representative. `config`
    is only passed by the deliberately labeled exploratory probe.
    """
    from vispy import app

    kwargs: dict[str, Any] = {'show': False, 'size': (width, 1)}
    if config:
        kwargs['config'] = config
    return app.Canvas(**kwargs)


def _gl_info() -> dict[str, str]:
    from vispy.gloo import gl

    def get(name):
        try:
            value = gl.glGetParameter(getattr(gl, name))
            return value if isinstance(value, str) else str(value)
        except Exception as e:  # noqa: BLE001 - reporting, not control flow
            return f'<{type(e).__name__}: {e}>'

    return {
        'gl_version': get('GL_VERSION'),
        'glsl_version': get('GL_SHADING_LANGUAGE_VERSION'),
        'vendor': get('GL_VENDOR'),
        'renderer': get('GL_RENDERER'),
    }


def _render(frag: str, data: np.ndarray, interpolation: str = 'nearest',
            out_width: int | None = None, uniforms: dict | None = None) -> np.ndarray:
    """Draw one quad sampling `data` and read the RGBA8 result back.

    Returns an (out_width, 4) uint8 array, one row of pixels.
    """
    from vispy import gloo

    n = data.shape[1]
    width = out_width or n
    tex = gloo.Texture2D(
        data, internalformat='r32f', interpolation=interpolation,
        wrapping='clamp_to_edge',
    )
    program = gloo.Program(VERT, frag)
    program['a_pos'] = np.array(
        [[-1, -1], [1, -1], [-1, 1], [1, 1]], dtype=np.float32
    )
    program['u_data'] = tex
    for key, value in (uniforms or {}).items():
        program[key] = value

    target = gloo.Texture2D(shape=(1, width, 4), format='rgba')
    fbo = gloo.FrameBuffer(color=target)
    with fbo:
        gloo.set_state(blend=False, depth_test=False)
        gloo.set_viewport(0, 0, width, 1)
        gloo.clear(color='black')
        program.draw('triangle_strip')
        out = gloo.read_pixels((0, 0, width, 1), alpha=True)
    return out[0]


# --------------------------------------------------------------------------
# Probe suites


@probe('env')
def probe_env() -> dict[str, Any]:
    """What context did we get, and what does it claim to support?"""
    from vispy.gloo import gl

    with _canvas(1):
        info = _gl_info()
        try:
            ext_blob = gl.glGetParameter(gl.GL_EXTENSIONS) or ''
        except Exception:  # noqa: BLE001
            ext_blob = ''
        extensions = set(str(ext_blob).split())
        info['n_extensions'] = len(extensions)
        info['has_ARB_shader_bit_encoding'] = (
            'GL_ARB_shader_bit_encoding' in extensions
        )
        info['has_ARB_texture_float'] = any(
            e in extensions
            for e in ('GL_ARB_texture_float', 'GL_APPLE_float_pixels')
        )
        info['has_ARB_color_buffer_float'] = (
            'GL_ARB_color_buffer_float' in extensions
        )
        # Whether an r32f texture can be created at all decides whether any of
        # this matters: without it napari's float path is already lossy.
        try:
            _render(
                'varying vec2 v_tex;\nuniform sampler2D u_data;\n'
                'void main() { gl_FragColor = vec4(texture2D(u_data, v_tex).r, 0, 0, 1); }',
                np.array([[0.5]], dtype=np.float32),
            )
            info['r32f_texture'] = PASS
        except Exception as e:  # noqa: BLE001
            info['r32f_texture'] = f'{FAIL}: {type(e).__name__}: {e}'
    return {'environment': info}


CLASS_FRAG = f"""
uniform sampler2D u_data;
varying vec2 v_tex;
void main() {{
    float v = texture2D(u_data, v_tex).r;
    // vispy's own NaN idiom, from _APPLY_CLIM_FLOAT
    bool vispy_nan = !(v <= 0.0 || 0.0 <= v);
    bool ne_self   = v != v;
    bool not_eq    = !(v == v);
    bool gt_max    = v > {FLT_MAX_LIT};
    bool lt_min    = v < -{FLT_MAX_LIT};
    bool zero_mul  = (v * 0.0) != 0.0;
    bool big_abs   = abs(v) > 1.0e38;
    // Self-comparisons are what a fast-math compiler folds away. This one is
    // not a self-comparison: (v * 0) is NaN for NaN and for either infinity,
    // so subtracting the two infinity tests leaves NaN alone.
    bool nan_composite = (v * 0.0) != 0.0 && !gt_max && !lt_min;

    float flagbits = 0.0;
    if (vispy_nan) flagbits += 1.0;
    if (ne_self)   flagbits += 2.0;
    if (not_eq)    flagbits += 4.0;
    if (gt_max)    flagbits += 8.0;
    if (lt_min)    flagbits += 16.0;
    if (zero_mul)  flagbits += 32.0;
    if (big_abs)   flagbits += 64.0;
    if (nan_composite) flagbits += 128.0;

    // Class as a chain built from whichever tests actually work here
    float code;
    if (nan_composite) code = 3.0;
    else if (gt_max) code = 2.0;
    else if (lt_min) code = 1.0;
    else             code = 4.0;

    gl_FragColor = vec4(flagbits / 255.0, code / 255.0, 0.0, 1.0);
}}
"""

IDIOMS = [
    ('vispy_nan', 1, lambda v: bool(np.isnan(v))),
    ('v != v', 2, lambda v: bool(np.isnan(v))),
    ('!(v == v)', 4, lambda v: bool(np.isnan(v))),
    ('v > FLT_MAX', 8, lambda v: bool(np.isposinf(v))),
    ('v < -FLT_MAX', 16, lambda v: bool(np.isneginf(v))),
    ('v * 0.0 != 0.0', 32, lambda v: bool(np.isnan(v) or np.isinf(v))),
    ('abs(v) > 1e38', 64, lambda v: bool(abs(np.float64(v)) > 1e38)),
    ('nan_composite', 128, lambda v: bool(np.isnan(v))),
]

# The idioms that would let a GLSL 1.20 shader classify a value without
# reading its bits. If one of each pair works, Option B's shader half is
# possible even where floatBitsToUint is not.
NAN_IDIOMS = ('vispy_nan', 'v != v', '!(v == v)', 'nan_composite')


@probe('comparisons')
def probe_comparisons(corrupt: bool = False) -> dict[str, Any]:
    """Which GLSL 1.20 idioms for detecting NaN and infinity survive this compiler?"""
    data = exp.data_array()
    with _canvas(data.shape[1]):
        pixels = _render(CLASS_FRAG, data)

    per_idiom: dict[str, dict[str, Any]] = {
        name: {'verdict': PASS, 'wrong': []} for name, _, _ in IDIOMS
    }
    classes: dict[str, Any] = {}
    for i, name in enumerate(exp.NAMES):
        value = exp.VALUES[name]
        flagbits = int(pixels[i, 0])
        for idiom, bit, truth in IDIOMS:
            got = bool(flagbits & bit)
            want = truth(value)
            if corrupt:  # --self-test: flip one expectation
                want = not want
            if got != want:
                per_idiom[idiom]['verdict'] = FAIL
                per_idiom[idiom]['wrong'].append(
                    {'value': name, 'got': got, 'expected': want}
                )
        got_class = int(pixels[i, 1])
        want_class = exp.expected_class(name)
        classes[name] = {
            'got': exp.CLASS_NAMES.get(got_class, got_class),
            'expected': exp.CLASS_NAMES[want_class],
            'verdict': PASS if got_class == want_class else FAIL,
        }

    # Class preservation through upload is only demonstrable for the classes
    # some working idiom can still see; where every idiom fails, the value's
    # fate through the upload is not observable by arithmetic at all.
    nan_visible = any(per_idiom[i]['verdict'] == PASS for i in NAN_IDIOMS)
    inf_visible = (
        per_idiom['v > FLT_MAX']['verdict'] == PASS
        and per_idiom['v < -FLT_MAX']['verdict'] == PASS
    )
    return {
        'idioms': per_idiom,
        'class_via_vispy_chain': classes,
        'working_nan_idioms': [i for i in NAN_IDIOMS if per_idiom[i]['verdict'] == PASS],
        'class_preserved_upload': {
            'nan': PASS if nan_visible else UNAVAILABLE,
            'infinities': PASS if inf_visible else UNAVAILABLE,
            'note': (
                'class-preserved is weaker evidence than bit-exact: it cannot '
                'distinguish "the value survived upload" from "the compiler '
                'folded the comparison". Where no idiom sees a class, this '
                'probe reports unavailable rather than fail.'
            ),
        },
    }


BITS_BODY = """
uniform sampler2D u_data;
varying vec2 v_tex;
void main() {
    float v = texture2D(u_data, v_tex).r;
    uint bits = floatBitsToUint(v);
    gl_FragColor = vec4(
        float(bits & 0xFFu) / 255.0,
        float((bits >> 8) & 0xFFu) / 255.0,
        float((bits >> 16) & 0xFFu) / 255.0,
        float((bits >> 24) & 0xFFu) / 255.0
    );
}
"""

# Tried in order, all in the context napari actually gets. A compile failure
# here is the finding: it means Option A is unavailable to napari on this
# platform without changing how napari creates its context.
PREAMBLES = [
    ('bare', ''),
    ('ext_pragma', '#extension GL_ARB_shader_bit_encoding : require\n'),
    ('version_130', '#version 130\n'),
    ('version_130_ext', '#version 130\n#extension GL_ARB_shader_bit_encoding : enable\n'),
]


@probe('bit_readback')
def probe_bit_readback() -> dict[str, Any]:
    """Can the shader read a float's bits? That is Option A's precondition.

    Doubles as the bit-exact upload-integrity test: if the bits come back, we
    know exactly what the upload preserved, NaN payload and subnormal included.
    """
    data = exp.data_array()
    attempts: dict[str, Any] = {}
    working: str | None = None
    pixels = None
    with _canvas(data.shape[1]):
        info = _gl_info()
        for label, preamble in PREAMBLES:
            try:
                pixels = _render(preamble + BITS_BODY, data)
                attempts[label] = {'verdict': PASS}
                working = label
                break
            except Exception as e:  # noqa: BLE001 - compile failure is a result
                message = str(e).strip().splitlines()
                attempts[label] = {
                    'verdict': UNAVAILABLE,
                    'error': ' | '.join(m.strip() for m in message[:6]),
                }

    result: dict[str, Any] = {
        'context': info,
        'attempts': attempts,
        'option_a_available_in_napari_context': working is not None,
        'working_preamble': working,
    }
    if working is None or pixels is None:
        result['bit_exact_upload'] = {
            'verdict': UNAVAILABLE,
            'reason': (
                'floatBitsToUint is unavailable in the context napari creates, '
                'so bit preservation cannot be observed from the shader.'
            ),
        }
        return result

    per_value = {}
    for i, name in enumerate(exp.NAMES):
        got = int(
            pixels[i, 0] | (pixels[i, 1] << 8) | (pixels[i, 2] << 16) | (pixels[i, 3] << 24)
        )
        want = exp.expected_bits(name)
        per_value[name] = {
            'got': f'0x{got:08X}',
            'expected': f'0x{want:08X}',
            'verdict': PASS if got == want else FAIL,
        }
    result['bit_exact_upload'] = {
        'verdict': PASS if all(v['verdict'] == PASS for v in per_value.values()) else FAIL,
        'per_value': per_value,
    }
    return result


STOCK_FRAG = """
uniform sampler2D u_data;
uniform vec2 u_clim;
varying vec2 v_tex;

// verbatim from vispy.visuals.image._APPLY_CLIM_FLOAT
float apply_clim(float data) {
    if (!(data <= 0.0 || 0.0 <= data)) return data;
    data = clamp(data, min(u_clim.x, u_clim.y), max(u_clim.x, u_clim.y));
    data = (data - u_clim.x) / (u_clim.y - u_clim.x);
    return data;
}

void main() {
    float t = apply_clim(texture2D(u_data, v_tex).r);
    // what the colormap does next: clamp(t, 0, 1) into a black-to-white LUT
    float gray = clamp(t, 0.0, 1.0);
    gl_FragColor = vec4(gray, gray, gray, 1.0);
}
"""


@probe('stock_baseline')
def probe_stock_baseline() -> dict[str, Any]:
    """What does napari's current pipeline display for each value, today?

    This is the ground truth for the `fallback` state of the Stage 4b support
    record: it is what users on this GPU actually see right now.
    """
    data = exp.data_array()
    with _canvas(data.shape[1]):
        pixels = _render(STOCK_FRAG, data, uniforms={'u_clim': (0.0, 1.0)})

    per_value = {}
    for i, name in enumerate(exp.NAMES):
        got = int(pixels[i, 0])
        want = exp.expected_stock_gray(name)
        if want is None:
            verdict = UNAVAILABLE  # spec-undefined; we record, we do not judge
        else:
            verdict = PASS if abs(got - want) <= 1 else FAIL
        per_value[name] = {
            'gray': got,
            'expected_gray': want,
            'verdict': verdict,
        }
    return {
        'clim': [0.0, 1.0],
        'per_value': per_value,
        'note': (
            'expected_gray is null for NaN: apply_clim passes NaN through and '
            'clamp() with a NaN argument is undefined in GLSL. The observed '
            'gray is what this driver happens to do.'
        ),
    }


FILTER_FRAG = f"""
uniform sampler2D u_data;
varying vec2 v_tex;
void main() {{
    float v = texture2D(u_data, v_tex).r;
    bool gt_max = v > {FLT_MAX_LIT};
    bool lt_min = v < -{FLT_MAX_LIT};
    bool is_inf = gt_max || lt_min;
    bool is_nan = (v * 0.0) != 0.0 && !gt_max && !lt_min;
    float code = is_nan ? 3.0 : (is_inf ? 2.0 : 4.0);
    // carry the finite magnitude too, so poisoning that shows up as a wrong
    // finite value rather than a NaN is still visible
    gl_FragColor = vec4(code / 255.0, clamp(v, 0.0, 1.0), 0.0, 1.0);
}}
"""


@probe('filtering')
def probe_filtering() -> dict[str, Any]:
    """How far does one NaN texel spread under linear interpolation?

    Option A classifies in the shader after filtering, so a NaN that poisons
    its neighbors makes exact per-pixel classification impossible at any zoom
    other than 1:1. Option B's sanitized data plus a nearest-sampled class
    texture is the answer if poisoning is wide.
    """
    # texels: 4 finite, one NaN, 4 finite, one +inf, 2 finite
    row = np.array(
        [[0.1, 0.2, 0.3, 0.4, np.nan, 0.6, 0.7, 0.8, 0.9, np.inf, 0.5, 0.5]],
        dtype=np.float32,
    )
    n = row.shape[1]
    samples = n * 8  # 8 samples per texel
    with _canvas(samples):
        linear = _render(FILTER_FRAG, row, interpolation='linear', out_width=samples)
        nearest = _render(FILTER_FRAG, row, interpolation='nearest', out_width=samples)

    def spread(pixels, code):
        hits = [i for i in range(samples) if int(pixels[i, 0]) == code]
        if not hits:
            return {'samples': 0, 'texel_span': 0.0}
        return {
            'samples': len(hits),
            'texel_span': round((max(hits) - min(hits) + 1) / 8.0, 3),
            'first_texel': round(min(hits) / 8.0, 3),
            'last_texel': round(max(hits) / 8.0, 3),
        }

    return {
        'texels': [('nan' if np.isnan(v) else 'inf' if np.isinf(v) else float(v))
                   for v in row[0]],
        'samples_per_texel': 8,
        'nan_source_texel': 4,
        'inf_source_texel': 9,
        'linear': {'nan': spread(linear, 3), 'inf': spread(linear, 2)},
        'nearest': {'nan': spread(nearest, 3), 'inf': spread(nearest, 2)},
        'note': (
            'Under nearest sampling a class should occupy exactly one texel '
            '(span 1.0). A wider span under linear sampling is interpolation '
            'poisoning, and bounds how much of the image Option A would '
            'misclassify when zoomed in.'
        ),
    }


@probe('exploratory_core_profile')
def probe_exploratory_core() -> dict[str, Any]:
    """EXPLORATORY ONLY: is a core-profile context reachable, and does it help?

    napari asks for no particular context, and vispy's Canvas config exposes no
    version or profile keys, so the only way to get a core profile is to set
    Qt's default surface format before any context exists. A pass here is not
    evidence that napari can use Option A: it says what would become possible
    if napari changed how it creates contexts, which is far larger than this
    work contemplates and would drop compatibility-profile features napari and
    vispy still rely on.
    """
    requested = {'major': 3, 'minor': 3, 'profile': 'core'}
    try:
        from qtpy.QtGui import QSurfaceFormat

        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)
    except Exception as e:  # noqa: BLE001
        return {
            'context_requested': requested,
            'verdict': UNAVAILABLE,
            'error': f'{type(e).__name__}: {e}',
            'caveat': 'exploratory; napari does not request this context',
        }

    data = exp.data_array()
    core_frag = (
        '#version 330\n'
        'uniform sampler2D u_data;\n'
        'in vec2 v_tex;\n'
        'out vec4 frag;\n'
        'void main() {\n'
        '    float v = texture(u_data, v_tex).r;\n'
        '    uint bits = floatBitsToUint(v);\n'
        '    frag = vec4(float(bits & 0xFFu) / 255.0,\n'
        '                float((bits >> 8) & 0xFFu) / 255.0,\n'
        '                float((bits >> 16) & 0xFFu) / 255.0,\n'
        '                float((bits >> 24) & 0xFFu) / 255.0);\n'
        '}\n'
    )
    core_vert = (
        '#version 330\n'
        'in vec2 a_pos;\n'
        'out vec2 v_tex;\n'
        'void main() {\n'
        '    v_tex = (a_pos + 1.0) * 0.5;\n'
        '    gl_Position = vec4(a_pos, 0.0, 1.0);\n'
        '}\n'
    )
    global VERT
    saved, VERT = VERT, core_vert
    try:
        with _canvas(data.shape[1]):
            info = _gl_info()
            try:
                pixels = _render(core_frag, data)
                bit_shader, error = PASS, None
            except Exception as e:  # noqa: BLE001
                pixels, bit_shader = None, UNAVAILABLE
                error = str(e).strip().splitlines()[0]
    except Exception as e:  # noqa: BLE001
        return {
            'context_requested': requested,
            'verdict': UNAVAILABLE,
            'error': f'{type(e).__name__}: {e}',
            'caveat': 'exploratory; napari does not request this context',
        }
    finally:
        VERT = saved

    result = {
        'context_requested': requested,
        'context_obtained': info,
        'bit_shader': bit_shader,
        'error': error,
        'caveat': 'exploratory; napari does not request this context',
    }
    if pixels is not None:
        result['bit_exact_upload'] = {
            name: {
                'got': f'0x{int(pixels[i, 0] | (pixels[i, 1] << 8) | (pixels[i, 2] << 16) | (pixels[i, 3] << 24)):08X}',
                'expected': f'0x{exp.expected_bits(name):08X}',
                'verdict': PASS if int(
                    pixels[i, 0] | (pixels[i, 1] << 8) | (pixels[i, 2] << 16) | (pixels[i, 3] << 24)
                ) == exp.expected_bits(name) else FAIL,
            }
            for i, name in enumerate(exp.NAMES)
        }
    return result


# --------------------------------------------------------------------------
# Driver


def _slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-') or 'unknown'


def run_one(name: str, corrupt: bool = False) -> dict[str, Any]:
    fn = _PROBES[name]
    try:
        return fn(corrupt=corrupt) if name == 'comparisons' else fn()
    except Exception:  # noqa: BLE001 - a crashing probe must still report
        return {'verdict': ERROR, 'traceback': traceback.format_exc()}


def run_in_subprocess(name: str, corrupt: bool = False) -> dict[str, Any]:
    cmd = [sys.executable, str(Path(__file__).resolve()), '--probe', name]
    if corrupt:
        cmd.append('--corrupt-expectations')
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith(SENTINEL):
            return json.loads(line[len(SENTINEL):])
    return {
        'verdict': ERROR,
        'returncode': proc.returncode,
        'stderr': proc.stderr[-2000:],
        'stdout': proc.stdout[-2000:],
    }


def self_test() -> int:
    """A check that cannot fail is worthless. Prove this one can."""
    honest = run_in_subprocess('comparisons')
    corrupt = run_in_subprocess('comparisons', corrupt=True)
    honest_fails = sum(
        1 for v in honest.get('idioms', {}).values() if v['verdict'] == FAIL
    )
    corrupt_fails = sum(
        1 for v in corrupt.get('idioms', {}).values() if v['verdict'] == FAIL
    )
    print(f'idiom failures with true expectations:    {honest_fails}')
    print(f'idiom failures with inverted expectations: {corrupt_fails}')
    if corrupt_fails < len(IDIOMS):
        print('SELF-TEST FAILED: inverted expectations did not fail every idiom.')
        return 1
    print('SELF-TEST PASSED: the harness reports mismatches when they exist.')
    return 0


def summarize(results: dict[str, Any]) -> str:
    lines = []
    env = results.get('env', {}).get('environment', {})
    lines.append(f'  renderer   {env.get("renderer")}')
    lines.append(f'  GL / GLSL  {env.get("gl_version")} / {env.get("glsl_version")}')
    lines.append(f'  r32f       {env.get("r32f_texture")}')

    bits = results.get('bit_readback', {})
    lines.append('')
    lines.append(f'  Option A in napari context: '
                 f'{"AVAILABLE" if bits.get("option_a_available_in_napari_context") else "UNAVAILABLE"}'
                 f' ({bits.get("working_preamble")})')
    lines.append(f'  bit-exact upload: {bits.get("bit_exact_upload", {}).get("verdict")}')

    comp = results.get('comparisons', {})
    lines.append('')
    lines.append('  GLSL 1.20 idioms:')
    lines.append(f'    usable NaN tests: {comp.get("working_nan_idioms")}')
    for idiom, info in comp.get('idioms', {}).items():
        wrong = ', '.join(w['value'] for w in info['wrong'][:4])
        lines.append(f'    {info["verdict"]:<12} {idiom:<16} {wrong}')

    stock = results.get('stock_baseline', {}).get('per_value', {})
    lines.append('')
    lines.append('  what napari shows today (clim 0-1, black-to-white):')
    for name in ('neg_inf', 'pos_inf', 'nan', 'nan_payload', 'denormal'):
        if name in stock:
            info = stock[name]
            lines.append(
                f'    {name:<12} gray={info["gray"]:<4} expected='
                f'{info["expected_gray"]}  {info["verdict"]}'
            )

    filt = results.get('filtering', {})
    if 'linear' in filt:
        lines.append('')
        lines.append(
            f'  linear-filter NaN spread: {filt["linear"]["nan"].get("texel_span")} texels '
            f'(nearest: {filt["nearest"]["nan"].get("texel_span")})'
        )
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', choices=sorted(_PROBES))
    parser.add_argument('--corrupt-expectations', action='store_true',
                        help='invert the CPU expectations (used by --self-test)')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--out', type=Path, default=None)
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.probe:
        result = run_one(args.probe, corrupt=args.corrupt_expectations)
        print(SENTINEL + json.dumps(result))
        return 0

    results: dict[str, Any] = {}
    for name in ('env', 'bit_readback', 'comparisons', 'stock_baseline',
                 'filtering', 'exploratory_core_profile'):
        print(f'running {name} ...', file=sys.stderr)
        results[name] = run_in_subprocess(name)

    env = results.get('env', {}).get('environment', {})
    slug = _slug(f'{env.get("renderer", "unknown")}-{env.get("gl_version", "")}')
    out = args.out or RESULTS / f'{slug}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')

    print()
    print(summarize(results))
    print()
    print(f'wrote {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
