# GL capability probe for exceptional values

**Status:** Active
**Last updated:** 2026-08-22
**Scope:** napari GPU rendering of NaN, infinities, and masked values; Stage 4a of
the staged plan in `cmap/docs/dev/mcslab/napari_exceptional_rendering_plan.md`

## Context

cmap PR #151 gives a colormap separate colors for -inf, +inf, NaN, and masked
entries. napari's CPU `Colormap.map()` can express that. Its GPU path cannot:
vispy uploads raw float32, clamps in the shader, and looks the result up in a
LUT, so the only exceptional value it handles at all is NaN, through one idiom
in `_APPLY_CLIM_FLOAT`.

Two designs were on the table. Option A classifies values in the fragment
shader by reading their bits with `floatBitsToUint`. Option B sanitizes the
data on the CPU and carries a small sidecar class texture. Choosing between
them needs facts about real drivers, not spec reading, because the GLSL spec
leaves NaN behavior undefined and says nothing binding about what a compiler
may fold. This harness gets those facts.

## Running it

```bash
python gl_probe.py              # all suites, writes results/<renderer-slug>.json
python gl_probe.py --probe env   # one suite, JSON on stdout
python gl_probe.py --self-test   # prove the harness can report a failure
```

Each suite runs in its own subprocess: GL contexts are per-process, and a
driver that dies on a shader should take down only its own probe. Every verdict
compares against a value computed on the CPU in `expected.py` first.
`unavailable` is a real answer, not a missing one: it means the platform cannot
run the probe, which is itself evidence.

## Findings: Apple M5, macOS, GL 2.1 Metal - 90.5, GLSL 1.20

Full record in `results/apple-m5-2-1-metal-90-5.json`.

**Option A is not merely unavailable here, it is unreachable.** All four ways of
asking for `floatBitsToUint` are rejected in the context napari gets: GLSL 130
is "not supported", and `GL_ARB_shader_bit_encoding` is absent from all 133
extensions. Setting Qt's default surface format to 3.3 core before any context
exists does not help either: vispy still creates a 2.1 context (it logs
"Could not create NSOpenGLContext with shared context"). So bit classification
on Apple Silicon would require changing how napari and vispy create contexts,
which is a far larger change than this work, and would give up
compatibility-profile behavior they still use.

**vispy's current NaN test does not work on this driver.** The idiom in
`_APPLY_CLIM_FLOAT`, `!(data <= 0.0 || 0.0 <= data)`, returns false for NaN
here, as do `v != v` and `!(v == v)`. All three are self-comparisons, which is
exactly what a fast-math compiler folds; Metal enables fast math by default.
The user-visible consequence, from the stock-pipeline baseline probe: NaN
currently renders as the bottom of the colormap, not as `nan_color`. napari's
NaN support on Apple Silicon is broken today, before any of this work.

**There is a working NaN test that survives.** `(v * 0.0) != 0.0` is true for
NaN and for both infinities and is not a self-comparison, so the compiler keeps
it. Subtracting the two infinity tests isolates NaN:

```glsl
bool gt_max = v >  3.402823466e+38;   // +inf only
bool lt_min = v < -3.402823466e+38;   // -inf only
bool is_nan = (v * 0.0) != 0.0 && !gt_max && !lt_min;
```

This classifies all thirteen probe values correctly here, NaN payload included.
The infinity tests work unmodified. So a GLSL 1.20 shader can distinguish all
four classes on this platform without reading any bits.

**Uploads preserve the classes.** r32f textures work, and the class of every
probe value survives upload and nearest sampling. Bit-exactness is reported
`unavailable` rather than `pass`: without `floatBitsToUint` there is no way to
observe the payload or subnormal bits, so the harness does not claim more than
it can see.

**Linear filtering poisons exactly one texel on each side.** A single NaN texel
occupies 2.0 texels of output under linear sampling against 1.0 under nearest;
+inf behaves the same. Any classification done after filtering therefore
misclassifies a one-texel border around every exceptional value whenever the
image is magnified.

### What this means for the design

The Apple result argues for Option B's data half and against Option A's
mechanism, but not for the reason the plan anticipated. The blocker is not that
bit classification is slow, it is that the platform will not compile it at all.
Meanwhile the comparison-based classification that Option B would need anyway
works fine here, which makes a sanitized-data-plus-class-texture design the one
that can actually ship on this hardware. The filtering result reinforces it: a
nearest-sampled class texture is immune to the poisoning that any
classify-after-filtering scheme suffers.

One platform is not a decision. The same harness needs to run on at least an
NVIDIA and an AMD/Mesa machine before Option A is ruled out generally, since
those are the platforms where GLSL 130 is routinely available and where the
answer may well be the opposite.

## Deferred work

- Run on NVIDIA, AMD, Intel, and llvmpipe. Results are keyed by renderer slug
  and are meant to accumulate in `results/`.
- Probe the volume path. Everything here is the 2D image pipeline; the seven
  volume rendering modes accumulate values along a ray and are a separate
  question (Stage 5).
- Probe integer and uint16 textures. napari casts float64 to float32 before
  upload, but the label and integer paths have their own conversions.

## Next steps

Feed these results into the Option A / Option B decision in the staged plan,
then open the napari design issue that Stage 2 depends on. The issue should
lead with the NaN finding, which is a live bug independent of the cmap work.
