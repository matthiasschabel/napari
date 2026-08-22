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

Needs `numpy`, `vispy`, and a Qt binding. It does not import napari, so anyone
with a working vispy can run it without a napari checkout.

```bash
python gl_probe.py               # all suites, writes results/<renderer-slug>.json
python gl_probe.py --probe env   # one suite, JSON on stdout
python gl_probe.py --self-test   # prove the harness can report a failure
```

Each suite runs in its own subprocess: GL contexts are per-process, and a
driver that dies on a shader should take down only its own probe. Every verdict
compares against a value computed on the CPU in `expected.py` first.
`unavailable` is a real answer, not a missing one: it means the platform cannot
run the probe, which is itself evidence.

`--self-test` does two things. It inverts the CPU expectations and checks every
idiom then reports failure, and it feeds synthetic pixels through the
bit-readback decoding. The second matters because the bit path cannot run on a
GLSL 1.20 platform at all: without it, a bug there would first surface on a
volunteer's machine after they had already spent the effort.

## Platform coverage

One platform does not decide a design. This is what we have and what we need.

| Platform | GL / GLSL | Status |
|---|---|---|
| Apple M5, macOS, GL 2.1 Metal | 2.1 / 1.20 | done, `results/apple-m5-2-1-metal-90-5.json` |
| Apple Silicon, other chip generations | expected 2.1 / 1.20 | **pending**; `macos-15` CI runners can supply this |
| Intel Mac, Apple GL over AMD/Intel | expected 2.1 / 1.20 | **pending**; `macos-13` CI runners, while they exist |
| Mesa llvmpipe, Linux x86_64 and arm64 | expected 4.5 / 4.50 | **pending**; CI, software rasterizer only |
| Mesa3D offscreen, Windows | expected 4.5 / 4.50 | **pending**; CI, software rasterizer only |
| NVIDIA, any OS | expected >= 3.3 / 3.30 | **pending, needs a volunteer**; unreachable from CI |
| AMD driver, Linux or Windows | expected >= 3.3 / 3.30 | **pending, needs a volunteer**; unreachable from CI |
| Intel integrated, Linux or Windows | expected >= 3.3 / 3.30 | **pending, needs a volunteer**; unreachable from CI |

The three volunteer rows are the ones that matter most for the design, because
they are where `floatBitsToUint` is expected to be available and where the
Apple answer may well be reversed. No amount of CI substitutes for them:
GitHub-hosted runners have no discrete GPU, so their Linux and Windows legs
measure Mesa's software rasterizer, not a vendor driver.

`ci_probe_workflow.yml` in this directory is a ready but inactive workflow. Copy
it to `.github/workflows/` on a fork and dispatch it to collect the CI rows.

### Contributing a result

Three commands, no napari needed:

```bash
pip install numpy vispy PyQt5
python gl_probe.py --self-test   # must print SELF-TEST PASSED
python gl_probe.py               # writes results/<your-renderer>.json
```

Send the JSON file. It records GL vendor, renderer, version, and extension
count, and nothing else about the machine: no hostname, no user, no paths.

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

**A working NaN test exists, but it is not the obvious one, and finding it
required testing inside a real shader.** The isolated-idiom probe reported
`(v * 0.0) != 0.0 && !gt_max && !lt_min` as working. It is not: put the same
expression in a chain beside the infinity tests and it folds to false. Under
the no-NaN assumption the compiler can reason that `v * 0.0` differs from zero
only for an infinity, and that the infinity tests then exclude it, so the whole
conjunction is provably false. Isolation hid that because the surrounding code
gave it less to prove with.

The mechanism, once visible, rules out a whole family at once. Any form
comparing against a **single** bound folds, uniform or literal, because
`v <= X || X <= v` is provable for every X. What survives is **two bounds the
compiler cannot relate**:

```glsl
uniform float u_flt_max;   // a uniform, so its sign is unknown at compile time
bool is_nan = !(v <= u_flt_max) && !(v >= -u_flt_max);
```

Refuting this requires knowing `u_flt_max >= -u_flt_max`, which a uniform
denies. It classifies all thirteen probe values correctly here, NaN payload
included, and the infinity tests need no change. So a GLSL 1.20 shader can
distinguish all four classes on this platform without reading any bits.

The general lesson is bigger than the expression: **no in-shader NaN test is
guaranteed**, because a compiler assuming no NaN exists is entitled to fold any
of them. This one works because of what this compiler happens to prove, not
because it is correct by construction. That is why the harness tests the
shipped chain rather than an idiom, and why a runtime check belongs in any
design that depends on it.

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

**The proposed napari chain passes end to end.** The `napari_chain` probe
compiles apply_clim, apply_gamma, and the colormap function as napari would
generate them, and checks that all thirteen values reach the right color. It
passes; the stock chain, run in the same process for contrast, misroutes
neg_inf, pos_inf, and both NaNs. This is now implemented in the fork.

### What this means for the design

The Apple result argues for Option B's data half and against Option A's
mechanism, but not for the reason the plan anticipated. The blocker is not that
bit classification is slow, it is that the platform will not compile it at all.
Meanwhile the comparison-based classification that Option B would need anyway
works fine here, which makes a sanitized-data-plus-class-texture design the one
that can actually ship on this hardware. The filtering result reinforces it: a
nearest-sampled class texture is immune to the poisoning that any
classify-after-filtering scheme suffers.

One platform is not a decision, and the platforms that would settle it are the
ones we cannot reach: see the coverage table above. Until an NVIDIA or AMD
result arrives, "Option A is unavailable" is a statement about Apple Silicon
and nothing more.

## Deferred work

- Fill in the coverage table. The CI rows need someone to push the draft
  workflow on a fork and dispatch it; the vendor-driver rows need volunteers.
  Results are keyed by renderer slug and accumulate in `results/`.
- Probe the volume path. Everything here is the 2D image pipeline; the seven
  volume rendering modes accumulate values along a ray and are a separate
  question (Stage 5).
- Probe integer and uint16 textures. napari casts float64 to float32 before
  upload, but the label and integer paths have their own conversions.

## Next steps

The NaN finding does not wait on the coverage table. It is a live bug on the
platform we can already measure, with a one-line fix, and it is independent of
everything else here, so it can go upstream on its own. Asking for the missing
platform rows is also a reasonable thing to do in that same issue: maintainers
and other contributors have the hardware we lack, and a self-contained script
that prints a verdict is a cheap thing to ask someone to run.

The Option A / Option B decision does wait. Until a vendor-driver result
arrives, the honest position is that Option B is the only design demonstrated
to work on hardware we have measured, not that Option A is ruled out.
