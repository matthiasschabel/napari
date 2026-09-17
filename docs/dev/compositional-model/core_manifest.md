# Compositional core extraction manifest

**Status:** Active
**Last updated:** 2026-09-17
**Scope:** the `compositional-core` branch to be cut from `908b3ed80`; the
file, export and test lists the extraction is executed and checked against.
Companion to [core_split_design.md](core_split_design.md) section 2.

## Context

The design table in `core_split_design.md` section 2 names the module
boundary as intent. This manifest verifies it by static analysis of the tree
at `6ca842774` (this worktree's commit) and records, per file, what the core
branch carries. Method: every `import`/`from` statement in
`src/napari/experimental/_data_model/` and its `_tests/` was classified as
module level, `TYPE_CHECKING` only, or function local, and the closure of the
retained public exports was walked by hand from that list. No test was run for
this document; the acceptance section says what to run.

## Verdict on the design table

| Claim in section 2 | Result |
|---|---|
| 27 modules in the core column separate cleanly | Holds. No core module imports an excluded module at any level (module, `TYPE_CHECKING`, or function local). |
| 19 modules in the exploration column | Holds. Every excluded module either imports only core modules or other excluded modules. |
| "Mixed tests split" (section 1) | Did not hold for `test_mpr.py` and `test_mpr_qt.py`. Both imported `CachingSource` and `MPRStreaming` at module level, so both would have failed at collection on the core branch. Split on 2026-09-17; see section 4.3. |
| "Prune `__init__.py` and `__init__.pyi` to match" | 16 `submod_attrs` entries and the same 16 stub blocks go. See section 3. |
| "~80 LOC outside `experimental/_data_model`" | Undercounts. The carried set is about 270 LOC of napari source (the two overlay pairs alone are 212), 310 LOC of napari tests, and 290 LOC of examples, once `test_qt_viewer.py`, `experimental/__init__.pyi`, the `pyproject.toml` testing extra, and the two MPR demos are included. See sections 2.1 and 2.2. |
| "17 upstream commits since `908b3ed80`" (section 3, step 5) | Stale. This branch has since merged upstream main to `5443f3715`, 22 commits past `908b3ed80`. `18ffd37dc` is an ancestor of `5443f3715`, so the accounting in step 5 must use `git diff --name-only 908b3ed80 5443f3715`. |
| "None of the upstream commits touch the canvas, slicer or overlay-registration files" | Holds for all 22: `git log 908b3ed80..5443f3715` is empty for `_vispy/canvas.py`, `components/_layer_slicer.py`, `components/overlays/__init__.py`, `_vispy/utils/visual.py`, `experimental/__init__.py` and `_qt/_tests/test_qt_viewer.py`. Three of them do touch `pyproject.toml` (`cf8b11921`, `dc6f5824f`, `5f1d9dee4`) and one touches `components/overlays/base.py` (`dc6f5824f`, comment-only pyrefly markers). Expect a trivial `pyproject.toml` conflict at the `integration` merge. |
| "the core keeps shapely off its import path" (section 4) | True only for the entry points `test_core_imports.py` exercises (`DataObject`, `Field`, `ArraySource`, `ImageBinding`). `_geometry.py` raises `ImportError` without shapely, and `_graph.py`, `_annotation.py`, `_shapes_codec.py`, `_measure.py`, `_mpr.py` and, through `_graph`, `_xarray_adapter.py` import it at module level. `FrameRegistry`, `data_object_from_xarray`, ROI and MPR therefore require shapely on the core branch. Consistent with pirana declaring `shapely>=2.0`; recorded so nobody reads section 4 as "the DICOM path is shapely-free". |

## 1. Source modules

### 1.1 Retained (27 modules plus the two package files)

One line per file. "Reached by" names the retained module(s) whose module-level
imports pull the file in; "export only" means nothing else in the core imports
it and it is retained because the package exports it.

| Module | Module-level imports inside `_data_model` | `TYPE_CHECKING` only | Function local | Reached by |
|---|---|---|---|---|
| `__init__.py` | `lazy_loader` attach map (section 3) | | | entry point |
| `__init__.pyi` | stub of the same map | | | entry point |
| `_annotation` | `_axis`, `_data_object`, `_geometry`, `_validation` | | | `_measure`, `_mpr` |
| `_axis` | `_validation` | | | `_domain`, `_field_geometry`, `_geometry`, `_graph`, `_mapping`, `_mesh_domain`, `_mri`, `_selection`, `_xarray_adapter`, `_layer_adapters`, `_annotation`, `_basis_conversion` |
| `_basis_conversion` | `_axis`, `_field`, `_source` | `_level_geometry` | `pint` | export only |
| `_data_object` | `_domain`, `_field`, `_mapping`, `_validation` | | `_index_selection` (`isel`, `sel`) | `_annotation`, `_graph`, `_image_binding`, `_index_selection`, `_layer_adapters`, `_measure`, `_mpr`, `_mri`, `_selection`, `_view_bridge`, `_xarray_adapter` |
| `_derived` | `_source` | `_level_geometry` | | `_mri` |
| `_display_sampling` | `_index_selection`, `_level_geometry`, `_source` | | | `_view_bridge` |
| `_domain` | `_axis` | | | `_data_object`, `_mapping`, `_index_selection`, `_image_binding`, `_layer_adapters`, `_measure`, `_mpr`, `_mri`, `_view_bridge`, `_xarray_adapter` |
| `_field` | `_field_geometry`, `_source`, `_validation` | | | `_data_object`, `_basis_conversion`, `_layer_adapters`, `_mri`, `_selection`, `_view_bridge`, `_xarray_adapter` |
| `_field_geometry` | `_axis`, `_validation` | | | `_field` |
| `_geometry` | `_axis`; `shapely` (hard `ImportError` if missing) | | | `_annotation`, `_graph`, `_measure`, `_mpr`, `_shapes_codec` |
| `_graph` | `_axis`, `_data_object`, `_geometry`, `_mapping` | | | `_xarray_adapter` |
| `_image_binding` | `_data_object`, `_domain`, `_view_bridge` | `napari.layers`, `numpy` | | export only |
| `_index_selection` | `_data_object`, `_domain`, `_mapping`, `_source` | | | `_display_sampling`; `_data_object` (local) |
| `_layer_adapters` | `_axis`, `_data_object`, `_domain`, `_field`, `_mapping`, `_source` | `napari.layers` | `napari.layers` | export only |
| `_level_geometry` | none (numpy only) | | | `_source`, `_display_sampling` |
| `_mapping` | `_axis`, `_domain` | | | `_data_object`, `_graph`, `_index_selection`, `_layer_adapters`, `_measure`, `_mri`, `_view_bridge`, `_xarray_adapter` |
| `_measure` | `_annotation`, `_data_object`, `_domain`, `_geometry`, `_mapping`, `_selection`, `_source` | | | export only |
| `_mesh_domain` | `_axis`, `_source` | | | export only |
| `_mpr` | `_annotation`, `_data_object`, `_domain`, `_geometry`, `_shapes_codec`, `_view_bridge`; `napari.components.overlays` (`SceneLineOverlay`, `SceneMeshOverlay`); `napari.layers.shapes._shapes_models.polygon` | `_field`, `_source`, `napari.layers`, `napari.utils.events` | `napari.components`, `napari.layers`, `napari.settings` | `_mpr_qt`; export |
| `_mpr_qt` | `_mpr`; `qtpy`; `napari._qt.qt_viewer`; `napari.layers` | `napari.components` | | not exported; imported by examples and tests |
| `_mri` | `_axis`, `_data_object`, `_derived`, `_domain`, `_field`, `_mapping`, `_source` | | | export only |
| `_selection` | `_axis`, `_data_object`, `_field` | | | `_measure` |
| `_shapes_codec` | `_geometry` | | | `_mpr` |
| `_source` | `_level_geometry` | | | nearly everything |
| `_validation` | none | | `pint` | `_axis`, `_data_object`, `_field`, `_field_geometry`, `_annotation`, `_xarray_adapter` |
| `_view_bridge` | `_data_object`, `_display_sampling`, `_domain`, `_field`, `_mapping`, `_source` | `napari.layers` | `napari.layers` | `_image_binding`, `_mpr` |
| `_xarray_adapter` | `_axis`, `_data_object`, `_domain`, `_field`, `_graph`, `_mapping`, `_source`, `_validation` | `xarray`, `_axis` | `xarray` | export only |

Two leftovers to know about, neither a boundary violation:

- `_view_bridge.py` line 239 mentions `ProgressiveSource` in a docstring.
- `_graph.py` line 88 has a `CorrespondenceBasis.ATLAS_BY_CONSTRUCTION` enum
  member. That is coordinate-graph vocabulary, unrelated to `_atlas.py`.

### 1.2 Excluded (19 modules)

| Module | Module-level imports inside `_data_model` | Why it stays behind |
|---|---|---|
| `_atlas` | `_bricks`, `_level_geometry`, `_progressive`, `_source` | GPU atlas |
| `_atlas_pane` | `_atlas`, `_atlas_vispy`, `_bricks`, `_ladder`, `_profile`, `_progressive`; `napari._vispy.utils.gl`; `qtpy`; `vispy` (`TYPE_CHECKING`: `_field`, `_mapping`, `_mpr`, `_mpr_qt`) | GPU atlas |
| `_atlas_vispy` | `_atlas`, `_level_geometry`; `vispy` | GPU atlas |
| `_bricks` | `_source` | multiresolution |
| `_caching_source` | `_source` (`TYPE_CHECKING`: `_bricks`, `_level_geometry`; local: `_bricks`) | streaming cache |
| `_downsample` | `_caching_source`, `_source`; local `tensorstore` | multiresolution |
| `_ladder` | `_bricks`, `_field`, `_mapping`, `_source` | multiresolution |
| `_level_subset` | `_caching_source`, `_data_object`, `_source` (`TYPE_CHECKING`: `_level_geometry`) | multiresolution |
| `_mpr_streaming` | `_caching_source`, `_prefetch`, `_profile`, `_progressive`, `_source`, `_working_set` (`TYPE_CHECKING`: `_mpr`, `_source`, `napari.utils.events`) | MPR streaming session |
| `_mri_readout_adapter` | `_axis`, `_data_object`, `_field`, `_field_geometry`, `_readout_domain`, `_source`, `_validation` | raw acquisition |
| `_ngff_adapter` | `_axis`, `_caching_source`, `_data_object`, `_domain`, `_field`, `_graph`, `_level_geometry`, `_mapping`, `_source`; local `zarr`, `tensorstore` | microscopy |
| `_pet_event_adapter` | `_axis`, `_data_object`, `_domain`, `_field`, `_field_geometry`, `_source`, `_validation` | raw acquisition |
| `_prefetch` | `_source` | streaming |
| `_profile` | `_atlas`, `_source` | interaction profile, imports the atlas |
| `_progressive` | `_bricks`, `_caching_source`, `_source` (`TYPE_CHECKING`: `_level_geometry`) | streaming |
| `_readout` | `_field` | raw acquisition |
| `_readout_access` | `_data_object`, `_field`, `_readout`, `_readout_domain`, `_source` | raw acquisition |
| `_readout_domain` | `_axis` | raw acquisition |
| `_working_set` | `_caching_source`, `_domain`, `_ladder`, `_mapping`, `_source` | streaming |

Reverse check: no excluded module is imported by a retained module. The only
cross-boundary edges point from excluded to retained, which is the intended
direction.

## 2. Files outside `_data_model`

Derived from `git diff --stat 5443f3715 6ca842774` (our changes relative to
the merged upstream tip) and `git log --name-only 5443f3715..6ca842774`.

### 2.1 Retained source (carry to `compositional-core`)

| File | Change | Reason |
|---|---|---|
| `src/napari/components/overlays/scene_line.py` | new, 38 lines | model overlay for MPR crosshairs and ROI traces |
| `src/napari/components/overlays/scene_mesh.py` | new, 37 lines | model overlay for ROI fill meshes |
| `src/napari/components/overlays/__init__.py` | +4 | registers the two overlays |
| `src/napari/_vispy/overlays/scene_line.py` | new, 72 lines | vispy overlay |
| `src/napari/_vispy/overlays/scene_mesh.py` | new, 65 lines | vispy overlay |
| `src/napari/_vispy/utils/visual.py` | +6 | `overlay_to_visual` registration |
| `src/napari/_vispy/canvas.py` | 3 guard hunks, +11/-2 | `_reorder_layers_in_the_same_view`, `_update_scenegraph` and `_setup_layer_views_in_grid` tolerate layers whose visual is not built yet |
| `src/napari/components/_layer_slicer.py` | 1 line | `tuple()` snapshot of the futures view in `wait_until_idle` |
| `src/napari/experimental/__init__.py` | rewritten, 19 lines | lazy `link_layers` import so importing the data model does not load `napari.layers` |
| `src/napari/experimental/__init__.pyi` | new, 5 lines | stub for the lazy init |
| `pyproject.toml` | +2 | `shapely>=2.0` in both `testing` extras; retained tests import shapely |
| `examples/dev/mpr_demo.py` | new, 27 lines | imports only `MPRController`, `synthetic_mri`, `_mpr_qt` |
| `examples/dev/mpr_dicom_demo.py` | new, 267 lines | imports only retained exports plus `_mpr_qt` and `_view_bridge._embedding_coordinates` |

`src/napari/_qt/qt_viewer.py` appears in our commit history but has no net
diff against upstream. Nothing to carry.

### 2.2 Retained tests outside `_data_model`

| File | Change | Note |
|---|---|---|
| `src/napari/components/_tests/test_scene_geometry_overlays.py` | new, 61 lines | overlay models |
| `src/napari/_vispy/_tests/test_vispy_scene_geometry_overlays.py` | new, 165 lines | vispy overlays; uses `FontInfo` and `create_vispy_overlay`, both present at `908b3ed80` |
| `src/napari/_qt/_tests/test_qt_viewer.py` | +79/-9 | two hunks belong to the canvas guards (`test_create_non_empty_viewer_model` parametrized over grid mode, `test_create_non_empty_viewer_model_with_visible_scene_overlay`). The third hunk (`_NativeMouseMoveFilter`, `qt_viewer_without_pointer_moves`, and the two drag-to-zoom tests switched to it) is a test-flakiness fix unrelated to the split. Decided: carry the whole diff. It is test-only, it keeps the file identical on both branches, and splitting it would leave the flaky tests flaky on `integration`. |

### 2.3 Excluded (stay on the exploration branch)

- `examples/dev/atlas_bench.py`
- `examples/dev/bench_progressive_mosaic.py`
- `examples/dev/benchmark_ngff_access.py`
- `examples/dev/mpr_ngff_demo.py` (imports `_atlas`, `_level_subset`, `_atlas_pane`)
- `examples/dev/transcode_ngff.py` (imports `_ngff_adapter`)
- `examples/dev/perf/` (all 10 files)
- `docs/dev/compositional-model/`: only `core_split_design.md` and this
  manifest travel with the core, because they describe what `integration` now
  holds. Every other note is exploration history and stays.

Napari internals the retained code and tests use were checked against the
`908b3ed80` tree and all exist there: `expand_corners_to_chunk_boundaries`,
`read_only_mouse_event`, `_LayerSlicer.wait_until_idle` and `_force_sync`,
`FontInfo`, `ViewerOverlayMixin`, `VispySceneOverlay`, the polygon shape
model, and the three canvas lines the guards edit.

## 3. Export pruning

### 3.1 `__init__.py` (`lazy.attach` `submod_attrs`)

Delete these 16 keys with all their names (46 names):

| Key | Names |
|---|---|
| `_atlas` | `MAX_LEVELS`, `AtlasFormat`, `AtlasLayout`, `AtlasManager`, `Uploader`, `atlas_format` |
| `_bricks` | `AxisAll`, `AxisPoint`, `AxisRange`, `AxisReduce`, `AxisSelection`, `BrickGrid`, `BrickKey`, `BrickProvenance`, `BrickRequest`, `BrickSet`, `ReduceOp`, `apply_reductions`, `brick_nbytes`, `brick_region`, `plan_bricks` |
| `_caching_source` | `CachingSource` |
| `_downsample` | `DownsampledSource` |
| `_ladder` | `LevelLadder`, `LevelRung`, `level_factors`, `section_request`, `select_ladder`, `target_spacing_for_section`, `virtual_factors` |
| `_mpr_streaming` | `MPRStreaming` |
| `_mri_readout_adapter` | `data_object_from_mri_readouts` |
| `_ngff_adapter` | `data_object_from_ngff` |
| `_pet_event_adapter` | `data_object_from_pet_events` |
| `_prefetch` | `SlicePrefetcher` |
| `_profile` | `AxisMotion`, `InteractionProfile`, `atlas_layout_for` |
| `_progressive` | `ProgressiveSource` |
| `_readout` | `Readout` |
| `_readout_access` | `read_readout` |
| `_readout_domain` | `ReadoutDomain` |
| `_working_set` | `WorkingSetEntry`, `WorkingSetManager`, `WorkingSetPlan`, `plan_working_set` |

Keep these 23 keys unchanged (48 names):

| Key | Names |
|---|---|
| `_annotation` | `ROI`, `PlaneAnchor`, `ROICollection` |
| `_axis` | `AxisDtype`, `AxisRole`, `CoordinateAxis`, `CoordinateFrame`, `FrameAxis` |
| `_basis_conversion` | `convert_field_basis` |
| `_data_object` | `DataObject` |
| `_derived` | `DerivedSource` |
| `_domain` | `Domain`, `StructuredGridDomain` |
| `_field` | `Field`, `Interpolation` |
| `_field_geometry` | `FieldGeometry` |
| `_geometry` | `Polygon`, `PolygonSetDomain` |
| `_graph` | `CoordinateGraph`, `CoordinateTransform`, `CorrespondenceBasis`, `FrameRegistry`, `MappingEdge`, `ResolvedPath`, `Unplaced` |
| `_image_binding` | `ImageBinding` |
| `_layer_adapters` | `data_object_from_layer`, `layer_from_data_object` |
| `_level_geometry` | `LevelGeometry` |
| `_mapping` | `AffineMapping`, `CoordinateEmbedding` |
| `_measure` | `mean_over_roi` |
| `_mesh_domain` | `MeshDomain` |
| `_mpr` | `MPRController` |
| `_mri` | `synthetic_mri` |
| `_selection` | `CoordinateSelection` |
| `_shapes_codec` | `polygon_to_shape_vertices`, `shape_vertices_to_polygon` |
| `_source` | `ArraySource`, `DataSource`, `MultiscaleSource`, `SourceChangedError`, `source_level_geometry`, `source_revision` |
| `_view_bridge` | `add_to_viewer` |
| `_xarray_adapter` | `XarrayEmbedding`, `data_object_from_xarray`, `embedding_from_reference_frame` |

`_display_sampling`, `_index_selection`, `_validation` and `_mpr_qt` have no
entries today and get none.

### 3.2 `__init__.pyi`

Delete the 16 `from napari.experimental._data_model.<module> import (...)`
blocks for the same keys as 3.1 (line ranges at `6ca842774`: `_atlas` 6-13,
`_caching_source` 21-23, `_bricks` 27-43, `_downsample` 54-56, `_ladder`
80-88, `_mpr_streaming` 107-109, `_mri_readout_adapter` 111-113,
`_ngff_adapter` 114-116, `_pet_event_adapter` 117-119, `_prefetch` 120-122,
`_profile` 123-127, `_progressive` 128-130, `_readout` 131, `_readout_access`
132-134, `_readout_domain` 135-137, `_working_set` 156-161). The remaining 23
blocks must list exactly the 48 names in 3.1; the acceptance script in
section 5 checks that.

## 4. Test partition (`_data_model/_tests/`, 45 files after the split)

### 4.1 Retained (26, including the two new split-support files)

| File | Note |
|---|---|
| `test_axis_domain.py` | |
| `test_basis_conversion.py` | |
| `test_core_imports.py` | boundary test; its blocked-module list names modules that no longer exist on core, which is harmless and keeps the guard useful after the merge back |
| `test_derived.py` | |
| `test_field_association.py` | |
| `test_field_geometry.py` | |
| `test_geometry_annotation.py` | imports shapely at module level |
| `test_graph.py` | |
| `test_image_binding.py` | one test skips without `pytestqt` |
| `test_index_selection.py` | |
| `test_layer_adapters.py` | imports `napari.layers` |
| `test_level_geometry.py` | |
| `test_mapping_selection.py` | |
| `test_measure.py` | |
| `test_mesh_domain.py` | |
| `test_mpr.py` | split; the streaming half is `test_mpr_streaming.py` (4.3) |
| `test_mpr_qt.py` | split; the streaming half is `test_mpr_qt_streaming.py` (4.3); Qt tests skip without bindings |
| `utils.py` | helpers shared by the MPR halves (4.3) |
| `conftest.py` | the `headless_vispy` fixture (4.3) |
| `test_mri.py` | |
| `test_mri_end_to_end.py` | uses `ViewerModel` |
| `test_mri_smoke.py` | |
| `test_shapes_codec.py` | imports `napari.layers.Shapes` |
| `test_source_field_object.py` | uses `_source.source_level_shards`, retained |
| `test_view_bridge.py` | uses `expand_corners_to_chunk_boundaries` from napari, present at base |
| `test_xarray_adapter.py` | |

### 4.2 Excluded (19)

| File | Reason when not obvious from the name |
|---|---|
| `test_acquisition_adapters.py` | raw MRI/PET readout adapters |
| `test_atlas.py` | |
| `test_atlas_bench.py` | drives `examples/dev/atlas_bench.py` |
| `test_atlas_pane.py` | |
| `test_atlas_vispy.py` | |
| `test_bricks.py` | |
| `test_caching_source.py` | |
| `test_downsample.py` | `importorskip('tensorstore')` at module level |
| `test_ladder.py` | |
| `test_level_subset.py` | |
| `test_ngff_adapter.py` | |
| `test_prefetch.py` | |
| `test_profile.py` | `_profile` is exploration only |
| `test_progressive.py` | |
| `test_readout_access.py` | raw acquisition |
| `test_transcode_ngff.py` | drives `examples/dev/transcode_ngff.py` |
| `test_working_set.py` | |
| `test_mpr_streaming.py` | the streaming half of `test_mpr.py` (4.3) |
| `test_mpr_qt_streaming.py` | the streaming half of `test_mpr_qt.py` (4.3) |

### 4.3 The MPR test split (done 2026-09-17)

Both files imported excluded names at module level, so on the core branch they
would have failed at collection, taking the whole file with them. They were
split on this branch, so the exploration branch keeps every test and the core
branch gets files that collect.

Result: `test_mpr.py` 66 tests and `test_mpr_streaming.py` 14 (80 before);
`test_mpr_qt.py` 11 and `test_mpr_qt_streaming.py` 15 (26 before). Two new
non-test files carry what both halves need: `_tests/utils.py` (`CountingSource`,
`_three_dimensional_data_object`, `_four_dimensional_data_object`,
`_wait_for_positions`, moved out of `test_mpr.py`) and `_tests/conftest.py`
(the `headless_vispy` fixture, moved out of `test_mpr_qt.py`). Both are
retained by the core. Suite after the split: 1370 passed in
`src/napari/experimental`, the same count as before it.

`test_mpr_default_slicing_updates_model_pane` stayed in core with its
`streaming=` argument dropped: its source is a plain `MultiscaleSource`, so the
session was inert and the assertion is about synchronous slicing. The lists
below record what moved.

**`test_mpr.py`** (2274 lines). Remove `CachingSource` and `MPRStreaming` from
the package import block (lines 16, 25) and the `_mpr_streaming` import of
`_prefetch_window` and `_source_level_steps` (lines 34-37). Move these 15
tests, and the `DelayedMultiscaleSource` helper that only they use, into a new
exploration-only file (suggested name `test_mpr_streaming.py`):

| Line | Test | Needs |
|---|---|---|
| 346 | `test_mpr_progressive_view_returns_resident_pixels_before_fine_arrives` | `CachingSource`, `MPRStreaming` |
| 407 | `test_mpr_temporal_neighbor_uses_resident_full_resolution_frame` | `CachingSource`, `MPRStreaming` |
| 465 | `test_mpr_progressive_false_preserves_blocking_view_reads` | `CachingSource`, `MPRStreaming(progressive=False)` |
| 491 | `test_mpr_residency_clamps_other_layer_extent_and_keeps_syncing` | `CachingSource`, `MPRStreaming`, `_source_level_steps` |
| 533 | `test_mpr_start_failure_closes_progressive_workers` | `CachingSource`, `_mpr_streaming` module attributes |
| 587 | `test_mpr_streaming_prepare_failure_releases_its_progressive_source` | `CachingSource`, `_mpr_streaming.ProgressiveSource` |
| 702 | `test_mpr_prefetches_neighboring_cached_slices` | `CachingSource`, `MPRStreaming(prefetch_radius=...)` |
| 737 | `test_mpr_prefetch_uses_full_window_for_undrawn_reordered_panes` | `_prefetch_window` |
| 763 | `test_mpr_prefetch_window_includes_zoomed_tile_endpoints` | `_prefetch_window` |
| 1998 | `test_mpr_streaming_rejects_invalid_option_types` | `MPRStreaming` |
| 2018 | `test_mpr_streaming_rejects_invalid_prefetch_values` | `MPRStreaming` |
| 2219 | `test_profile_controls_only_explicit_resident_window` | `CachingSource`, `MPRStreaming(profile=...)`, `InteractionProfile`, `AxisMotion` |
| 2255 | `test_controller_rejects_profile_rank_mismatch` | `MPRStreaming(profile=...)`, `InteractionProfile` |
| 2267 | `test_controller_rejects_profile_type` | `MPRStreaming(profile=...)` |

The session-contract tests at 648 and 669 use the `_RecordingSession` fake and
stay in core. `CountingSource` is used by retained tests and stays.

**`test_mpr_qt.py`** (1110 lines). Remove `CachingSource` and `MPRStreaming`
from the package import block (lines 16, 24). Move the helpers
`CountingMultiscaleSource`, `GatedMultiscaleSource`, `GatedCachingSource`,
`_multiscale_mpr_data_object`, `_gated_working_set_controller` (no retained
test uses them) and these 15 test functions into an exploration-only file
(suggested name `test_mpr_qt_streaming.py`):

| Line | Test | Needs |
|---|---|---|
| 140 | `test_mpr_async_slicing_completes_and_closes_owned_slicers` | `MPRStreaming` |
| 181 | `test_mpr_async_startup_locks_coarse_until_viewers_exist` | `MPRStreaming` |
| 254 | `test_mpr_async_startup_waits_for_tier_zero_residency` | gated `CachingSource` |
| 305 | `test_mpr_async_startup_timeout_unlocks_without_tier_zero` | gated `CachingSource` |
| 360 | `test_mpr_close_during_tier_zero_wait_disconnects` | gated `CachingSource` |
| 404 | `test_mpr_async_startup_does_not_wait_for_resident_tier_zero` | `CachingSource`, `MPRStreaming` |
| 438 | `test_mpr_failed_enable_skips_deferral_and_unlocks` | gated `CachingSource` |
| 472 | `test_mpr_widget_can_leave_slicing_synchronous` | `MPRStreaming` |
| 570 | `test_mpr_widget_refreshes_after_progressive_arrival` | `CachingSource`, `MPRStreaming` |
| 669 | `test_caching_source_multiscale_layer_renders_read_only_slice` | `CachingSource` |
| 738 | `test_mpr_ngff_demo_rejects_non_integer_environment_variables` (3 params) | runs `examples/dev/mpr_ngff_demo.py`, excluded |
| 993 | `test_mpr_ngff_demo_rejects_nonpositive_zarr_concurrency` | same script |
| 1017 | `test_mpr_ngff_demo_rejects_unknown_array_backend` | same script |
| 1042 | `test_mpr_controller_level_lock_round_trip` | `MPRStreaming` |
| 1071 | `test_mpr_widget_construction_failure_unlocks_levels` | `MPRStreaming` |

Retained in core: `test_mpr_widget_honors_controller_async_slicing_false`
(505), the three option-validation tests (524, 537, 554),
`test_mpr_widget_coalesces_many_progressive_arrivals` (641, drives
`_on_progressive_arrival` directly with no session),
`test_data_model_package_import_does_not_import_qt` (705), the
`headless_vispy` and `mpr_widget` fixtures, and the six widget tests from 827
on.

## 5. Acceptance procedure

Run in the `compositional-core` worktree. `PY` is
`/opt/miniconda3/envs/scipy-dev/bin/python` with `PYTHONPATH=<worktree>/src`;
the repo `.venv` cannot run the suite.

### 5.1 File set matches this manifest

```sh
git diff --name-only 908b3ed80 compositional-core | sort > /tmp/core_actual.txt
```

Pass: the list is exactly the union of section 1.1, section 2.1, section 2.2
and section 4.1, and nothing from section 1.2, 2.3 or 4.2 appears. A one-line
grep proves the excluded modules are physically absent, not merely unexported:

```sh
ls src/napari/experimental/_data_model/ | grep -E '^_(atlas|atlas_pane|atlas_vispy|bricks|caching_source|downsample|ladder|level_subset|mpr_streaming|mri_readout_adapter|ngff_adapter|pet_event_adapter|prefetch|profile|progressive|readout|readout_access|readout_domain|working_set)\.py$'
```

Pass: no output. Same for the tests:

```sh
ls src/napari/experimental/_data_model/_tests/ | grep -E '^test_(acquisition_adapters|atlas|atlas_bench|atlas_pane|atlas_vispy|bricks|caching_source|downsample|ladder|level_subset|ngff_adapter|prefetch|profile|progressive|readout_access|transcode_ngff|working_set)\.py$'
```

Pass: no output.

### 5.2 No retained file names an excluded module

```sh
grep -rn -E '_data_model\._(atlas|atlas_pane|atlas_vispy|bricks|caching_source|downsample|ladder|level_subset|mpr_streaming|mri_readout_adapter|ngff_adapter|pet_event_adapter|prefetch|profile|progressive|readout|readout_access|readout_domain|working_set)\b' src examples | grep -v '_tests/test_core_imports.py'
```

Pass: no output. (`test_core_imports.py` legitimately lists the names in its
blocked tuple.)

### 5.3 Exports resolve and the stub matches

```sh
$PY - <<'EOF'
import ast, pathlib, sys
import napari.experimental._data_model as m
names = sorted(m.__all__)
for n in names:
    getattr(m, n)  # a stale submod_attrs entry raises ModuleNotFoundError here
stub = pathlib.Path(m.__file__).with_suffix('.pyi').read_text()
stub_names = sorted(
    a.asname or a.name
    for node in ast.parse(stub).body
    if isinstance(node, ast.ImportFrom)
    for a in node.names
)
assert names == stub_names, (set(names) ^ set(stub_names))
assert len(names) == 48, len(names)
excluded = {k for k in sys.modules if k.startswith('napari.experimental._data_model._') and k.rsplit('.', 1)[1] in {
    '_atlas', '_atlas_pane', '_atlas_vispy', '_bricks', '_caching_source', '_downsample', '_ladder',
    '_level_subset', '_mpr_streaming', '_mri_readout_adapter', '_ngff_adapter', '_pet_event_adapter',
    '_prefetch', '_profile', '_progressive', '_readout', '_readout_access', '_readout_domain', '_working_set'}}
assert not excluded, excluded
print('exports ok:', len(names))
EOF
```

Pass: prints `exports ok: 48`.

### 5.4 Import boundary

```sh
$PY -m pytest src/napari/experimental/_data_model/_tests/test_core_imports.py -p no:cacheprovider -q
```

Pass: 2 passed. The second test additionally proves `MPRController` builds
with `streaming is None` and that `_mpr_qt` imports when `qtpy` is present.

### 5.5 Retained suite collects and passes

Collection first, because a module-level import of a deleted name shows up
here as an error rather than as a failed test:

```sh
$PY -m pytest --collect-only -q src/napari/experimental/_data_model/_tests -p no:cacheprovider 2>&1 | tail -3
```

Pass: the summary line has no `error`. Then the run:

```sh
$PY -m pytest -p no:cacheprovider \
  src/napari/experimental/_data_model/_tests \
  src/napari/components/_tests/test_scene_geometry_overlays.py \
  src/napari/components/_tests/test_layer_slicer.py \
  src/napari/_vispy/_tests/test_vispy_scene_geometry_overlays.py \
  src/napari/_vispy/_tests/test_vispy_canvas_axes_overlay.py \
  src/napari/_qt/_tests/test_qt_viewer.py
```

Pass: zero failed, zero errors. Skips are acceptable only from the markers
already in the tree: `requires_qt` in `test_mpr_qt.py`, the `pytestqt` guard in
`test_image_binding.py`, and whatever napari's own conftest already skips in
`test_qt_viewer.py`. The exploration branch reported 1455 passed, 23 skipped
for the same paths with everything present; the core count is smaller by the
17 excluded files and the 30 moved test functions and will be recorded here
after the first run.

### 5.6 Exploration branch still green after the split (done)

```sh
$PY -m pytest -p no:cacheprovider src/napari/experimental/_data_model/_tests/test_mpr.py src/napari/experimental/_data_model/_tests/test_mpr_qt.py src/napari/experimental/_data_model/_tests/test_mpr_streaming.py src/napari/experimental/_data_model/_tests/test_mpr_qt_streaming.py
```

Ran 2026-09-17: 130 passed across the four files, and 1370 across
`src/napari/experimental`, matching the pre-split count.

## Deferred Work

None. The three decisions this manifest opened (the default-slicing test, the
drag-to-zoom hunk, and which notes travel) are recorded above.

## Next Steps

1. Build `compositional-core` and run sections 5.1 through 5.5. The file lists
   in sections 1, 2 and 4 are the input; section 5.1 is the check.
2. Merge it into `integration` and reconcile this branch, per
   `core_split_design.md` section 3.
