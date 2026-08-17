# Napari Shape Performance Summary

**Status:** Implemented
**Last updated:** 2026-08-17
**Scope:** Measured rendering gains for Shapes, Points, Vectors, and related raster-layer optimizations

## Context

This note summarizes the performance gains measured during the rendering audit.
Measurements used a macOS arm64 reference machine with a real Qt/VisPy canvas
and GPU-complete timing. Ratios are more portable than absolute timings. Rows
may include multiple changes from the combined optimization stack, so the gains
must not be multiplied together.

P0 added benchmark and regression coverage but did not itself change runtime
performance.

## Current Decision

Keep the implemented non-breaking P1 optimizations and the creation-only Shapes
staging path. Do not ship transparent-triangle omission: its gain is restricted
to a non-default blend mode and it regresses outline-only rendering.

### Measured optimization gains

| Optimization | Affected layer or primitive | Tested scale | Before | After | Effect |
|---|---|---:|---:|---:|---:|
| Hide empty main and auxiliary visuals | Shapes: lines, paths, polygons, and other Shapes primitives | 32 empty-slice layers | 243.55 ms | 22.02 ms | **11.1 times faster** |
| Hide empty and inactive visuals during camera redraw | Shapes | 2,048 shapes across 16 layers | 13.01 ms | 6.30 ms | **2.1 times faster** |
| Hide empty child visuals | Vectors | 16 empty-slice layers | 59.41 ms | 10.63 ms | **5.6 times faster** |
| Hide inactive child visuals | Points | 32,768 points across 16 layers | 11.57 ms | 7.40 ms | **1.6 times faster** |
| Preserve Mesh shaders and programs | Shapes | 16 layers over 20 slice frames | 64.1 ms/frame; 320 program builds | 15.7 ms/frame; 0 program builds | **4.1 times faster** |
| Preserve extent caches across slices | Shapes: one polygon per layer | 48 layers | 13.73 ms | 8.78 ms | **36 percent faster** |
| Cache unchanged displayed transforms | uint32 Labels | 20 layers | 21.84 ms; 400 matrix rebuilds | 17.77 ms; 0 matrix rebuilds | **18.6 percent faster** |
| Stage active creation outside aggregate geometry | Shapes: line, rectangle, ellipse, path, polyline, polygon, and lasso | 10,000 existing 32-segment paths | 76.54 ms | 1.67 ms | **45.9 times faster** on combined P0/P1; 12.3 times without P1 |
| Stage active creation outside aggregate geometry | Same Shapes primitives | 50,000 existing 32-segment paths | 409.55 ms | 2.87 ms | **142.8 times faster** on combined P0/P1; 53.8 times without P1 |

The combined one-shot commit for a new shape in a 50,000-path layer took 340 ms
versus 361 ms for the comparison path. Staging therefore removes total-layer
work from pointer frames without moving an additional stall to mouse release.

### Active-creation layer scaling

This ceiling probe held the total at 10,000 paths:

| Layers | Paths per layer | Active-frame time | Approximate FPS | Relative to one layer |
|---:|---:|---:|---:|---:|
| 1 | 10,000 | 3.0 ms | 333 | 1.0 times |
| 10 | 1,000 | 3.3 ms | 303 | 1.1 times |
| 100 | 100 | 10.0 ms | 100 | 3.3 times |

Total primitive count dominates through moderate layer counts. At 100 layers,
per-layer traversal and draw-call overhead becomes material but the measured
workload remains near 100 FPS.

### Scope limits

| Area | Current coverage |
|---|---|
| New Shapes creation | Accelerated for line, rectangle, ellipse, path, polyline, polygon, and lasso creation |
| Existing-shape movement and resizing | Not yet accelerated by staging |
| Multidimensional Shapes with non-displayed axes | Integration retains the established aggregate path to preserve slice-anchor behavior |
| Lines versus Vectors | Shapes line/path primitives use staging; Vector-layer glyphs benefit only from the empty/inactive visual changes |
| Points | Benefit from empty/inactive visual suppression, not Shapes staging |
| Image and Labels | Benefit from transform reuse; the isolated measured result is for 20 uint32 Labels layers |

## Alternatives Considered

### Transparent Shapes stream omission

| Workload in `translucent_no_depth` | Paired effect |
|---|---:|
| 4,096 filled rectangles, triangle-index filtering | 11.1 percent faster |
| 16,384 filled rectangles, triangle-index filtering | 7.0 percent faster |
| 32,768 filled rectangles, triangle-index filtering | 7.5 percent faster |
| 16,384 outline rectangles, triangle-index filtering | 1.9 percent slower |
| 4,096 overlapping filled rectangles | 2.5 percent faster |
| 4,096 filled rectangles, vertex compaction | 8.5 percent faster |

This optimization was deferred. Default translucent rendering and several
other blending modes cannot safely omit the same streams, camera-only frames do
not upload geometry, and outline-only rendering regressed.

## Deferred Work

- Add hideable committed ranges and partial GPU writes before accelerating
  existing-shape movement or resizing.
- Add multidimensional staging only with explicit slice-point and partition-
  change lifecycle coverage.
- Rebaseline the full fixed-total partition matrix on other operating systems
  and representative integrated and discrete GPUs.
- Add exact viewport culling only after instrumentation can prove its cutoff,
  invalidation cost, and pixel equivalence.

## Next Steps

1. Measure the same object and layer-count matrices on Linux, Windows, and a
   discrete GPU.
2. Instrument draw submissions, uploaded bytes, buffer allocations, and sampled
   GPU-complete time.
3. Design the existing-shape edit path around persistent, partially updateable
   committed ranges.

The detailed audit and methodology are in
[`napari-performance-analysis.md`](napari-performance-analysis.md). The staged
creation design and acceptance gates are in
[`shapes_active_edit_overlay.md`](shapes_active_edit_overlay.md).
