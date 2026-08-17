# Shapes Active-Edit Overlay Investigation

**Status:** Active
**Last updated:** 2026-08-16
**Scope:** Shapes creation interaction, aggregate CPU geometry, and VisPy mesh uploads

## Context

Issues napari/napari#8399 and #8650 report unusable movement and drawing once a
Shapes layer reaches 10,000 to 100,000 shapes. PR #8109 made stable per-shape
CPU ranges updateable in place, but each pointer frame still performs
total-layer work:

1. `ShapeList` changes one shape.
2. `_update_displayed()` gathers full displayed vertex, triangle, mapping, and
   color arrays.
3. `Shapes.refresh()` causes `VispyShapesLayer` to replace the full `MeshData`.
4. On draw, VisPy expands indexed faces to triangle vertices and uploads the
   full vertex and color buffers.

The existing ASV interaction cases stop at 256 six-vertex shapes and do not
create a VisPy canvas or wait for GPU completion, so they miss this path.

### Measurements

The probes use a hidden real Qt/VisPy canvas, deterministic 32-segment paths,
and a forced draw plus GL completion. The overlay ceiling keeps the large layer
immutable while updating a one-shape layer.

On the clean combined P0/P1 tree (`89861704`):

| Existing paths | GPU-complete operation | Current frame | Overlay frame | Gain |
|---:|---|---:|---:|---:|
| 10,000 | Shift one path | 45.0 ms | 2.6 ms | 17 times |
| 50,000 | Shift one path | 233.0 ms | 3.4 ms | 68 times |
| 10,000 | Grow a path from 2 to 32 vertices | 88.3 ms | 1.5 ms | 59 times |
| 50,000 | Grow a path from 2 to 32 vertices | 504.0 ms | 3.6 ms | 139 times |

Unmodified upstream/main (`7290750c`) independently reproduces the creation
ceiling:

| Existing paths | Current frame | Overlay frame | One-shot commit | Pointer gain |
|---:|---:|---:|---:|---:|
| 10,000 | 95.8 ms | 7.4 ms | 97.0 ms | 13 times |
| 50,000 | 466.4 ms | 8.4 ms | 467.7 ms | 56 times |

At 50,000 paths, a stable-shift frame on the combined tree divides into 73.7 ms
of model mutation and displayed-array construction, 8.5 ms of adapter work,
and 151.4 ms in the dirty draw and upload. The Shapes mesh payload is 112.4 MB;
VisPy materializes 18.6 million indexed vertex components on every dirty draw.

The 50,000-path CPU-only growth ablation shows why one narrow array change is
not sufficient:

| Mutation path | Median |
|---|---:|
| Current aggregate edit | 268.9 ms |
| Skip displayed-array rebuild | 80.0 ms |
| Also skip global z-order rebuild | 13.5 ms |
| Update only the active `Shape` object | 0.041 ms |

The combined renderer also meets the requested practical layer scaling. With
10,000 total paths split across 1, 10, and 100 immutable layers, the overlay
frame was 3.0, 3.3, and 10.0 ms. Primitive work dominates through moderate
layer counts; 100 layers exposes draw-call overhead but remains near 100 FPS on
the reference machine.

## Current Decision

Proceed with a non-breaking, creation-only first implementation. Keep the
committed aggregate mesh immutable while a line, rectangle, ellipse, path,
polyline, polygon, or lasso is being created. Existing-shape movement and
resizing remain a separate dependent change.

### Staged model representation

Keep the in-progress `Shape` in `ShapeList.shapes` so `layer.data`, `nshapes`,
selection, and the `ADDING` then `ADDED` event sequence remain live and
unchanged. Append its colors, z index, slice key, and zero-width aggregate range
boundaries, but no committed vertices or triangles.

The staged invariant differs from a committed shape: aggregate widths are zero
while the live `Shape` counts are authoritative. Track the single staged index.
Generic aggregate mutators must fail explicitly on it. The creation paths in
`_add_rectangle_ellipse_line` and `_move_active_element_under_cursor` instead
mutate the `Shape` object directly. `Mode.DIRECT` and
`_move_selected_layer` retain the committed path.

Do not append staged data to aggregate `_vertices`, which would retain
total-layer relocation during path growth. Vertex handles and hit-testing read
`shapes[staged_index].data_displayed` directly, including axis reversal and the
`ADD_POLYLINE` last-preview-vertex trim. GUI creation guarantees that the shape
is in the current slice.

At finish, populate the aggregate ranges inside one `batched_updates()` block,
rebuild z order and displayed arrays once, emit the existing completion events,
and refresh the main visual once. Invalid staged paths must remove their
zero-width ranges without materializing a mesh.

### Overlay rendering

Use a private layer scene overlay, not a temporary user-visible Shapes layer.
Its VisPy adapter reads the active `Shape` object's face and edge geometry plus
the layer's current colors and width. It must reproduce the committed path:

- edge vertices use `_edge_vertices + edge_width * _edge_offsets`;
- NumPy axes are reversed before VisPy submission;
- 2D layer geometry displayed in 3D is zero-padded;
- transform, visibility, opacity, and blending follow the owning layer.

GUI creation supplies no z index and `_add_shapes` assigns
`max(_z_index) + 1`, so the staged shape is strictly topmost and a trailing
overlay matches committed ordering. Make maximality a checked private
precondition and use the current path if a future caller violates it.

### Acceptance gates

Functional tests must preserve live data, selection, event order, geometry,
style, z order, invalid-shape cleanup, mode changes, slicing, and teardown. The
main Shapes Mesh must receive no data replacement between creation start and
finish, then exactly one committed update.

Performance gates for 32-vertex creation are:

- upstream/main: at least 10 times faster at 10,000 paths, 40 times at 50,000,
  and no more than 12 ms median at 50,000;
- combined P0/P1: at least 20 times faster at both sizes and no more than 8 ms
  median at 50,000;
- no total-shape-dependent CPU allocation or upload during intermediate frames;
- no regression above 5 percent for an empty or one-shape layer;
- commit no slower than 1.05 times the current GPU-complete frame on the same
  tree and workload.

The one-shot commit is not a new stall. At 50,000 paths it replaces one current
504 ms final frame with about 400 ms on the combined tree. A 30-update draw falls
from roughly 15 seconds of blocked pointer frames to about 0.5 seconds total.
Persistent committed buffers remain worthwhile because the release pause is
still visible.

The reviewed plan converged with no unresolved product or architecture issue.
The prototype remains conditional on the measured gates; no public API or
settings flag is justified.

## Alternatives Considered

- Optimizing `_update_displayed()` alone leaves about 80 ms of CPU work before
  any GPU upload at 50,000 paths.
- Exponential NumPy capacity alone leaves global z-order, displayed gathers,
  VisPy expansion, and full uploads in every pointer frame.
- A renderer-only overlay leaves 74 to 269 ms of main-thread model work.
- A temporary Shapes layer proves the ceiling but leaks into layer selection,
  events, serialization, grid layout, and plugin-visible layer lists.
- Existing-shape movement cannot join the first change: the committed shape
  would remain visible unless a GPU range can be hidden without a full upload.

## Deferred Work

1. Introduce capacity-managed persistent flattened vertex and color buffers
   with per-shape ranges and partial writes.
2. Hide one committed range at drag start, render it in the active overlay, and
   update only that range at release.
3. Add capacity growth and idle compaction for triangle-count changes.
4. Reconsider scheduling a large commit only under a separate design that
   preserves synchronous model and event semantics.
5. Validate on Linux, Windows, and representative integrated and discrete GPUs.

## Next Steps

1. Implement staged creation and the private overlay in the isolated feature
   tree.
2. Add focused ShapeList, mouse interaction, VisPy, and GPU-complete tests.
3. Review the implementation before committing or mirroring production code.
