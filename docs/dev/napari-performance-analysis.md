# Napari Rendering Performance Analysis

**Status:** Active  
**Last updated:** 2026-08-16  
**Scope:** Points, Shapes, Vectors, Image, and Labels rendering; layer-count and primitive-count scaling

## Context

napari targets high-performance scientific visualization, but geometric and raster
layers currently incur substantial CPU-side orchestration and GPU-resource churn.
The problem is particularly visible when data is distributed across many layers.

At a fixed total primitive count, splitting data across 16 layers instead of one
made Points 3.3 times slower and Shapes 2.2 times slower in this audit. Vectors
remained essentially flat at 1.07 times, demonstrating that good layer-count
scaling is achievable within the current VisPy renderer. Fixed-total raster
slicing became 2.4 to 3.5 times slower across 16 layers.

The primary bottlenecks are not insufficient GPU arithmetic throughput. They are:

- broad per-layer refreshes on every slice;
- repeated shader, program, and buffer invalidation;
- drawing empty and inactive compound visuals;
- transform recomputation when transforms have not changed;
- monolithic Shapes geometry updates; and
- missing viewport culling.

The desired performance model is:

\[
T \approx T_\text{base} + \alpha N_\text{layers}
  + \beta N_\text{visible primitives}
\]

The objective is to reduce \(\alpha\) to the unavoidable cost of compositing
distinct layer states, leaving total visible primitives, raster bytes, and pixel
fill as the dominant terms. Complete independence from layer count is impossible
when layers have different transforms, blend modes, clipping planes, or opacity,
but the Vectors results show that a 16-way split can remain nearly flat for
compatible workloads.

Related upstream reports and work include:

- [Slow rendering with many empty Points and Shapes layers (#6658)](https://github.com/napari/napari/issues/6658)
- [Historical Shapes performance investigation (#1562)](https://github.com/napari/napari/issues/1562)
- [Choppy interactive drawing with large Shapes layers (#8650)](https://github.com/napari/napari/issues/8650)
- [Large overlapping Points rendering (#2101)](https://github.com/napari/napari/issues/2101)
- [Large raster slice switching (#1300)](https://github.com/napari/napari/issues/1300)
- [Dense point aggregation proposal (#6148)](https://github.com/napari/napari/issues/6148)
- [GPU-backed array support (#2243)](https://github.com/napari/napari/issues/2243)
- [Avoid redundant extent unit conversion (#9411)](https://github.com/napari/napari/pull/9411)
- [Progressive chunked rendering (#9067)](https://github.com/napari/napari/pull/9067)

### Audit environment

Measurements used actual Qt and VisPy draw cycles on:

- napari `0.9.0a3.dev23+g7290750c9`;
- macOS arm64 with Metal-backed OpenGL 2.1;
- Python 3.12, PyQt6 6.10, VisPy 0.16.2;
- a 640 by 480 canvas;
- synchronous slicing; and
- full Qt event processing after every slice or camera update.

The audited checkout was `377cf59c`; the relevant rendering hot paths matched
upstream commit `7290750c`. Results are medians and should be interpreted as
comparative measurements rather than cross-platform absolute timings.

### Measured layer scaling

Fixed total primitives distributed across layers:

| Layer type | Total primitives | 1 layer | 16 layers | Slowdown |
|---|---:|---:|---:|---:|
| Points | 32,768 | 5.06 ms | 16.65 ms | 3.3 times |
| Shapes | 2,048 | 7.34 ms | 16.36 ms | 2.2 times |
| Vectors | 16,384 | 7.72 ms | 8.26 ms | 1.07 times |

Empty layers during z-slicing:

| Empty layers | Points | Shapes | Vectors |
|---:|---:|---:|---:|
| 0 | 6.85 ms | 6.51 ms | 4.73 ms |
| 8 | 15.52 ms | 67.62 ms | 37.77 ms |
| 16 | 23.45 ms | 127.60 ms | 66.51 ms |
| 32 | 39.11 ms | 250.84 ms | 124.69 ms |

This reproduces the central symptom in #6658. Shapes can fall below four frames
per second with 32 layers containing no visible geometry.

Fixed total displayed raster pixels distributed across layers:

| Data | 1 layer | 16 layers | Slowdown |
|---|---:|---:|---:|
| uint8 Image | 8.08 ms | 19.26 ms | 2.4 times |
| Binary Labels | 7.82 ms | 24.84 ms | 3.2 times |
| uint16 Labels | 7.87 ms | 21.22 ms | 2.7 times |
| uint32 Labels | 8.64 ms | 30.16 ms | 3.5 times |

### Bottlenecks

#### Broad slice invalidation

Every layer is submitted through
`src/napari/components/_layer_slicer.py::_LayerSlicer.submit`. A slice refresh
then includes data events, thumbnail work, highlight updates, and extent
invalidation through `Layer._slice_dims` and `Layer._refresh_sync` in
`src/napari/layers/base/base.py`.

`ViewerModel` also handles duplicate Dims notifications and reads aggregate
layer extent units during slice processing. In a model-only benchmark with 48
empty Shapes layers, replacing the broad refresh with a data-only event reduced
13.78 ms to 4.93 ms.

#### Renderer resource invalidation

`VispyShapesLayer._on_data_change` and `VispyVectorsLayer._on_data_change` call
`MeshVisual.set_data`. VisPy constructs new `MeshData` and replaces shader
functions, invalidating the program.

Profiling 20 empty Shapes layers over 20 frames observed:

- 400 layer data changes;
- 800 program-build calls;
- 1,600 shader-compilation calls; and
- 800 link calls.

Disabling only the visual data callback reduced the empty-Shapes workload by
46 percent.

#### Empty and inactive visual submission

Points is a compound visual with main markers, selection markers, highlight
lines, and text. Shapes has a main mesh, highlight mesh, line, markers, and text.
All subvisuals remain visible even when they have no meaningful payload.

Empty Shapes and Vectors receive dummy transparent triangles. Empty Points
receives a dummy invisible marker. These workarounds prevent VisPy errors, but
the nodes are still traversed and drawn.

In a 20-empty-Shapes-layer test:

| Configuration | Median slice time |
|---|---:|
| Normal | 182.54 ms |
| Hide empty main visuals | 17.11 ms |
| Hide empty visuals and suppress data callbacks | 16.36 ms |

The empty-node fast path alone removed approximately 91 percent of the cost.
Hiding inactive auxiliary visuals also reduced a 16-layer Shapes camera redraw
from 15.22 ms to 8.09 ms.

#### Redundant transform work

The canvas visits every layer and calls `_update_draw` on every draw. This
rebuilds displayed transforms and maps canvas corners. Scalar-field layers also
invoke `_on_matrix_change` from their data-change handler, although ordinary
z-slice changes do not alter displayed axes or transforms. For 20 uint32 Labels
layers, matrix handling took nearly as much profile time as the data update.

#### Monolithic Shapes geometry

Interactive addition in `src/napari/layers/shapes/_shape_list.py` repeatedly
appends to global NumPy arrays and updates z-order. The visual then reuploads the
displayed mesh. Adding or editing one shape therefore becomes proportional to
the total layer geometry, matching #8650.

The Shapes mesh also stores both face and edge geometry. A rectangle produced
the same 14 vertices and 10 triangles whether its face, edge, or neither was
transparent. Transparent streams therefore still consume triangulation time,
memory, upload bandwidth, and draw work.

#### Missing viewport culling

All primitives in the current n-dimensional slice are submitted even when far
outside the visible canvas. Large overlapping Points can additionally become
fragment-fill limited, as described in #2101. Exact rendering cannot eliminate
that GPU limit without culling or changing representation.

## Current Decision

The renderer should be optimized in place before considering a wholesale
backend replacement. Work should proceed in measured stages:

1. Add benchmarks that expose layer-distribution scaling and distinguish CPU
   submission from GPU-complete frame time.
2. Stop drawing empty and inactive visuals.
3. Preserve shaders, programs, buffers, and textures across normal updates.
4. Split broad layer refreshes into explicit invalidation domains.
5. Introduce incremental geometry and raster updates where profiling justifies
   the additional internal structure.

All work through the incremental-update stage can preserve public APIs and exact
rendered output. Screen-space aggregation, adaptive quality, GPU-native public
protocols, and a new renderer backend require separate maintainer decisions.

### P0/P1 implementation status

The P0 benchmark gate and the five measured P1 changes are implemented on the
local `integration` branch as separate reviewed commits:

| Stage | Integration commit | Result |
|---|---|---|
| P0 | `283c9ef5` | Added fixed-total partition matrices for Points, Shapes, Vectors, Image, binary Labels, and uint32 Labels, plus empty geometric slice cases. |
| P1-A | `1138ae4e` | Shapes independently hides empty face, edge, highlight, marker, and text subvisuals while preserving the compound parent for overlays. |
| P1-B | `3b6d5d37`, `d5b7717e` | Points and Vectors skip empty child visuals; Vectors gained an overlay-safe compound visual, including async-slicing coverage. |
| P1-C | `e4597adb` | napari's Mesh visual reuses the immutable color-transform shader function instead of replacing it on every update. |
| P1-D | `4aa25913` | Ordinary sync and async slices preserve full-data extent caches; full async refresh still invalidates before reloading. |
| P1-E | `520207ed` | Non-multiscale Image and Labels reuse unchanged displayed transforms; multiscale remains eager and overlay lifecycle owns child-transform initialization. |

All changes are private implementation or benchmark changes. No public API,
data model, or rendered-pixel compromise was introduced. Claude reviewed the
plan and each initial implementation through the collaborative-refinement
workflow; all blocking findings were resolved before mirroring to integration.

Measured results on the audit machine:

| Workload | Before | After | Effect |
|---|---:|---:|---:|
| 32 empty-slice Shapes layers | 243.55 ms | 22.02 ms | 11.1 times faster |
| 2,048 Shapes across 16 layers, camera redraw | 13.01 ms | 6.30 ms | 2.1 times faster |
| 16 empty-slice Vectors layers | 59.41 ms | 10.63 ms | 5.6 times faster |
| 32,768 Points across 16 layers | 11.57 ms | 7.40 ms | 1.6 times faster |
| 16 Shapes layers, 20 slice frames | 64.1 ms/frame and 320 program builds | 15.7 ms/frame and 0 program builds | 4.1 times faster; shader churn removed |
| 48 Shapes layers with one polygon each, model slice | 13.73 ms | 8.78 ms | 36 percent faster |
| 20 uint32 Labels layers, paired integration slice | 21.84 ms and 400 matrix rebuilds | 17.77 ms and 0 matrix rebuilds | 18.6 percent faster |

The combined touched-area suite passes 188 tests with 10 expected skips. A
rendered Labels screenshot and its visual transform were byte-for-byte and
matrix-for-matrix identical before and after P1-E.

The broader integration VisPy suite failures were pre-existing and unrelated to
this series: the direction-labels overlay, written against the pre-`Scene` API,
still read the deprecated `viewer.camera` property, and `error:::napari` turns
that `DeprecationWarning` into an error. The earlier count of five was an
artifact of the repository's `--maxfail=5`; the full count is 12 (all 11 tests
in `test_vispy_direction_labels_overlay.py` plus
`test_canvas.py::test_canvas_overlays`, which sees the same warning wrapped in a
`psygnal.EmitLoopError`). All 12 reproduce at the exact pre-P1-E commit. Porting
the overlay and its tests to `viewer.scene.camera` clears them: the VisPy suite
is 210 passed, 19 skipped, and the components suite is 567 passed. The
`feature/direction-labels` branch itself is unaffected because it is based on a
`main` that predates the `Scene` refactor (#9323); it needs this port when it is
rebased for upstream review.

### P2-1 transparent stream status

Fully transparent Shapes face and edge omission was investigated on the combined
P0/P1 integration renderer and deferred. The corrected probe explicitly draws
the VisPy scene, waits for GPU completion, records per-arm upload counts, and
compares offscreen pixels.

| Workload in `translucent_no_depth` | Paired effect |
|---|---:|
| 4,096 filled rectangles, triangle-index filtering | 11.1 percent faster |
| 16,384 filled rectangles, triangle-index filtering | 7.0 percent faster |
| 32,768 filled rectangles, triangle-index filtering | 7.5 percent faster |
| 16,384 outline rectangles, triangle-index filtering | 1.9 percent slower |
| 4,096 overlapping filled rectangles | 2.5 percent faster |
| 4,096 filled rectangles, vertex compaction | 8.5 percent faster |

The rendered pixels were byte-identical in every safe-mode case, and counters
proved that the expected triangles were removed. The gain is nevertheless
restricted to the non-default `translucent_no_depth` mode. Default translucent
rendering still writes depth, several other blending modes are provably
pixel-different, camera frames do not upload geometry, and the outline case
regressed. Vertex compaction also underperformed index-only filtering.

Construction does not justify a model-level change either. With the warmed
Bermuda backend, edge triangulation was 1.93 percent of total construction for
4,096 non-convex 20-vertex polygons; paired combined-minus-face time was 2.51
percent. Removing model geometry would still break hit testing, selection
outlines, and color-only style reactivation.

The decision, raw samples, exact commands, and reusable probes are retained in
`docs/dev/shapes_transparent_streams.md` and `tools/perfmon/`. Claude accepted
the production deferral after plan and implementation convergence review.

### P2-2 active-edit overlay evaluation

The active-edit overlay and incremental Shapes boundary is a high-yield target.
GPU-complete probes kept the large Shapes layer immutable while updating a
one-shape layer, reproducing the proposed private overlay without changing
production behavior.

On the combined P0/P1 renderer:

| Existing 32-segment paths | Operation | Current frame | Overlay frame | Gain |
|---:|---|---:|---:|---:|
| 10,000 | Shift one path | 45.0 ms | 2.6 ms | 17 times |
| 50,000 | Shift one path | 233.0 ms | 3.4 ms | 68 times |
| 10,000 | Grow a path from 2 to 32 vertices | 88.3 ms | 1.5 ms | 59 times |
| 50,000 | Grow a path from 2 to 32 vertices | 504.0 ms | 3.6 ms | 139 times |

Unmodified upstream/main independently produced 95.8 to 7.4 ms at 10,000
paths and 466.4 to 8.4 ms at 50,000 paths for shape growth. The one-shot
commits were 97.0 and 467.7 ms respectively, effectively one current final
frame rather than a new cost. On the combined renderer, a 30-update 50,000-path
draw falls from roughly 15 seconds of blocked pointer frames to about 0.5
seconds total, including the approximately 400 ms release-time commit.

The CPU ablation also rules out a narrow array patch. At 50,000 paths, growing
one shape took 268.9 ms in the current aggregate path. Skipping the displayed
gather left 80.0 ms; also skipping global z-order left 13.5 ms; mutating only
the active `Shape` took 0.041 ms. Total-layer work must leave the pointer path,
not merely become a somewhat faster copy.

The requested fixed-total scaling remains practical after P0/P1. With 10,000
paths split across 1, 10, and 100 immutable layers, overlay frames were 3.0,
3.3, and 10.0 ms. Primitive work dominates through moderate layer counts; 100
layers exposes draw-call overhead but remains near 100 FPS on the audit machine.

The reviewed creation-only implementation is now integrated. A staged shape
stays logically present so `layer.data`, selection, and `ADDING`/`ADDED` events
retain their current behavior, while zero-width committed ranges keep aggregate
vertices and triangles out of intermediate frames. The existing Shapes
highlight mesh renders active faces and edges before outlines and handles, so
the implementation adds no scene node or draw call. Finish performs one
aggregate commit. Vertex handles and hit-testing read the staged `Shape`
directly. The private path is limited to the existing topmost GUI z order; any
future non-maximal caller falls back to current rendering.

Final GPU-complete results pass every reviewed gate:

| Renderer | Existing paths | Aggregate frame | Staged frame | Gain |
|---|---:|---:|---:|---:|
| upstream/main | 10,000 | 82.92 ms | 6.73 ms | 12.3 times |
| upstream/main | 50,000 | 407.06 ms | 7.56 ms | 53.8 times |
| combined P0/P1 | 10,000 | 76.54 ms | 1.67 ms | 45.9 times |
| combined P0/P1 | 50,000 | 409.55 ms | 2.87 ms | 142.8 times |

At 50,000 paths, the combined one-shot commit is 340 ms versus 361 ms for the
comparison commit. Model mutation plus refresh remains about 0.2 ms at both
sizes, proving that pointer-frame CPU work is independent of total layer
geometry. Empty and one-shape A/B frames varied by 0.9 to 1.7 percent, below the
5 percent regression gate. The combined Shapes and VisPy suite passes 510
tests; its one failure is the pre-existing constructor docstring-order check in
integration-only lasso work and is outside the touched lines.

Existing-shape movement is a dependent change. An overlay alone would leave the
old committed geometry visible, while rebuilding the main mesh at mouse press
would only relocate the stall. Persistent GPU ranges and partial writes are
needed to hide and later update one committed shape without a full upload.

The reviewed decision, exact gates, and reusable probes are retained in
`docs/dev/shapes_active_edit_overlay.md` and `tools/perfmon/`; the production
implementation is integration commit `62bf44fa`. No human escalation or
rendered-pixel compromise was needed. Integration conservatively stages only
layers without non-displayed dimensions, preserving its existing
multidimensional drawing-anchor semantics.

### Recommended architecture

The renderer should move from broad "layer changed, reset the visual" operations
to versioned render payloads and persistent GPU resources.

```text
Layer semantic data
        |
        v
Slice coordinator
  produces immutable payload/delta
        |
        v
RenderPayload
  geometry/style/transform versions
  dirty ranges, empty state
        |
        v
Per-visual GPU resource owner
  persistent VBOs, textures, shaders
  capacity and subrange updates
        |
        v
Draw scheduler
  skips empty/inactive nodes
  groups compatible submissions
        |
        v
GPU
```

| Component | Responsibility | Ownership |
|---|---|---|
| Layer model | Semantic arrays, styles, and public behavior | Authoritative CPU data |
| Slice coordinator | Determine displayed data and render deltas | Immutable slice results |
| Render payload | Describe what changed | Versions, dirty ranges, transform key, empty flag |
| GPU resource owner | Apply deltas without rebuilding programs | Buffers, textures, and shader lifetime |
| Draw scheduler | Decide what must be submitted | Frame-local submission list and metrics |

The payload should initially remain a private dataclass. Independent version
keys should cover geometry, style, slice, transform, and overlay state. A camera
movement must not invalidate geometry; a slice change must not invalidate the
transform; an unselected layer must not rebuild selection overlays.

Async slicing should return immutable payloads. Only the main GL thread should
mutate GPU resources, and stale payload IDs should be discarded.

### Prioritized optimization plan

| Priority | Change | Evidence or expected payoff | Compatibility |
|---|---|---|---|
| P0 | Add partition-scaling and frame-completion benchmarks | Prevents optimizing only one-layer cases | Non-breaking |
| P1 | Hide empty main visuals and inactive selection, highlight, and text visuals | Up to 91 percent reduction for empty Shapes | Non-breaking |
| P1 | Keep Mesh shaders, programs, and buffers persistent | Data-callback ablation saved 46 percent | Non-breaking |
| P1 | Narrow slice invalidation | Model cost fell from 13.78 to 4.93 ms | Non-breaking |
| P1 | Cache displayed transforms and update only on transform changes | Major Labels multi-layer cost | Non-breaking |
| P2 (implemented for creation) | Stage new Shapes outside aggregate CPU geometry and render them through the highlight mesh | 45.9-142.8 times faster on combined P0/P1 | Non-breaking internally |
| P2 (deferred) | Omit fully transparent face and edge streams | Safe non-default mode gained 7-11 percent for filled layers but regressed outlines; model construction share was about 2 percent | Non-breaking only with blend and interaction semantics preserved |
| P2 | Add exact viewport culling above measured thresholds | Helps zoomed geometric workloads | Non-breaking when pixel-exact |
| P2 | Preserve raster textures and use partial or cancellable uploads | Addresses fixed-pixel multi-layer penalty | Non-breaking |
| P3 | Add optional screen-space aggregation and adaptive quality | Large dense-data gains | Changes visual behavior |
| P3 | Introduce a GPU-native array protocol or new backend | Removes structural CPU copies | Requires architecture and API discussion |

#### P0: Regression gates

The ASV suite should include a matrix over layer count and primitives per layer
while holding the total constant. Existing benchmarks predominantly exercise
one layer and do not detect the architectural issue.

Recommended acceptance conditions:

- A fixed-total geometric workload distributed across 16 layers should take no
  more than 1.5 times the one-layer time when compositing state is compatible.
- Steady-state slice and camera frames should perform no shader compilation or
  program linking.
- Empty and inactive visuals should produce no draw submission.
- Benchmarks should record CPU submission time and sampled GPU-complete time
  separately. GPU completion should be sampled periodically rather than forcing
  `glFinish` on every frame.
- Draw calls, uploaded bytes, buffer reallocations, and program rebuilds should
  be tracked where the backend exposes them.

#### P1: Low-risk, high-return fixes

These should be separate, reviewable changes:

1. Mark a visual inactive when its current rendered payload is empty. Retain
   dummy geometry internally only if VisPy requires it.
2. Hide empty highlight, selection, marker, and text subvisuals independently.
3. Replace repeated `MeshVisual.set_data` with persistent buffers and
   capacity-aware updates.
4. Move aggregate extent and unit computation out of the slice hot path. PR
   #9411 addresses redundant unit conversion, but it does not remove broad
   extent processing.
5. Split `_refresh_sync` into explicit invalidation domains.
6. Cache displayed inverse transforms and canvas-corner mappings until axes,
   transforms, units, or `ndisplay` actually change.

#### P2: Incremental data paths

New Shapes now stay outside aggregate CPU geometry while being created and use
the existing highlight mesh as their small dynamic stream. Existing-shape edits
still need persistent, capacity-managed committed buffers so one range can be
hidden and updated without rebuilding the whole layer. Commit can then update a
subrange; compaction can happen during idle time.

Raster work should preserve texture objects, avoid redundant transform and
colormap updates, and use partial texture updates when dirty regions are known.
PR #9067 contains useful chunk budgeting and partial-upload ideas, but its large
experimental architecture should not block smaller improvements to the current
path.

#### P3: Maintainer discussion required

Exact rendering of millions of large, overlapping translucent points remains
fill-rate bound. Screen-space aggregation, density rendering, or reduced
interactive quality can produce much larger gains, but they change pixels.
These should be explicit opt-in modes with a full-quality idle render.

GPU-native arrays and a WGPU or Metal-oriented backend could ultimately remove
CPU copies and legacy OpenGL constraints. That is a strategic project, not the
first response to #6658. The present renderer has substantial headroom once
program rebuilding and empty submissions are removed.

## Alternatives Considered

| Alternative | Simplicity | Extensibility | Testability | Migration cost |
|---|---|---|---|---|
| Surgical empty-node and invalidation fixes only | High | Low | High | Very low |
| Versioned payloads with persistent resource owners | Moderate | High | High | Incremental and private |
| Global cross-layer batcher | Low | Moderate | Difficult because of ordering and blending | High |
| Replace VisPy immediately | Very low | Potentially high | Expensive and platform-heavy | Very high |

The recommended approach is staged. Land surgical P1 improvements while
introducing only enough private payload and resource structure to support three
concrete users. A global batcher is premature. Vectors shows that a single
persistent mesh per layer already approaches the desired scaling without
cross-layer batching.

### Decision classification

| Decision | Reversible? | Cost |
|---|---|---|
| Benchmark and instrumentation additions | Yes | Low |
| Empty and inactive visual suppression | Yes | Low |
| Narrow invalidation and transform caching | Yes | Low to medium |
| Persistent private GPU resource ownership | Yes | Medium |
| Chunked Shapes storage | Mostly | Medium to high |
| Optional LOD or adaptive quality | Yes, but user-visible | High review cost |
| Public GPU-array protocol | Difficult | High |
| New rendering backend | No practical quick rollback | Very high |

### Scope boundaries

This audit covered:

- Points, Shapes, and Vectors;
- Image slices;
- Labels and binary masks;
- empty-layer behavior;
- camera redraws and z-slice changes;
- layer-count versus primitive-count scaling;
- fill and outline geometry; and
- CPU model events, VisPy updates, and GPU submission.

It did not redesign volume rendering or benchmark every operating system and
GPU. The initial audit made no source changes; the P0/P1 follow-up implemented
the low-risk stages described above.

### Risks and unknowns

- Measurements come from one Apple/Metal OpenGL configuration. Call counts and
  invalidation structure are platform-independent, but absolute gains require
  validation on NVIDIA, AMD, Intel, Mesa, Windows, and Linux.
- Profiling inflates timing, although shader-build and event counts remain
  conclusive.
- Transparency, blend order, clipping, and per-layer transforms impose real
  batching limits.
- Empty-node suppression must correctly reactivate nodes when data, text, or
  selection appears.
- Async slicing may move some work off the UI thread, but it does not eliminate
  redundant events, uploads, shader rebuilds, or draw calls.
- Exact viewport culling requires reliable bounds and invalidation for edits,
  transforms, and n-dimensional slicing.
- Large raster slices retain unavoidable upload-bandwidth and VRAM limits.

## Deferred Work

- Design an opt-in screen-space aggregation mode for dense Points and Shapes.
- Evaluate adaptive interactive quality separately from exact rendering paths.
- Define a GPU-native input and memory-ownership protocol only after the current
  upload and invalidation costs have been reduced and remeasured.
- Reconsider a renderer replacement only if persistent-resource improvements
  cannot meet the measured scaling targets on supported platforms.
- Validate all benchmark matrices on representative Windows, Linux, NVIDIA,
  AMD, Intel, and Mesa configurations.

## Next Steps

1. Run the committed partition matrices on representative Linux, Windows, and
   discrete-GPU systems before setting cross-platform regression thresholds.
2. Add instrumentation for draw submissions, upload bytes, buffer allocations,
   CPU submission time, and sampled GPU-complete time.
3. Rebaseline all partition matrices on the combined P0/P1 integration tree.
4. Design the dependent existing-shape edit path around hideable committed
   ranges and partial GPU writes; do not move the full-mesh stall to drag start.
5. Revisit transparent stream omission only after a render-payload boundary can
   preserve interaction geometry and make transition invalidation cheap.
6. Prototype exact viewport culling only after the instrumentation can prove
   both its cutoff and its invalidation cost.

P0/P1 is complete. The P2 transparent-stream item is measured and deferred. The
P2 Shapes active-edit item is implemented for creation and passes its upstream
and combined performance gates. Existing-shape movement is the next dependent
Shapes milestone.
