# Compositional core split

**Status:** Active
**Last updated:** 2026-09-17
**Scope:** `src/napari/experimental/_data_model/`, the `integration` branch, and
this exploration branch

## Context

This branch adds the compositional data model as ~18.1k LOC of source and
~23.3k LOC of tests under `experimental/_data_model/`. Changes to existing
napari files come to ~80 LOC. pirana, which consumes the `integration` branch,
works with medical imaging data. That data (MRI, CT, PET) generally fits in
memory, and napari's in-memory Image path reslices it adequately. The
multiresolution stack (bricks, ladder, working set, progressive reads,
prefetch, GPU atlas) serves out-of-core OME-NGFF microscopy, which is not a
pirana priority.

The goal is to bring the DataObject core, ROI/measurement and MPR into
`integration` while the multiresolution work stays here. Constraints:

- Touches to napari code outside `experimental/_data_model` stay light and
  contained.
- All of this code lives in our napari fork, not in pirana and not in a
  separate distribution.
- The DataObject/ROI model, kept separate from Layers, should stay presentable
  to a possibly revived napari architecture working group.

The static import graph showed that the core separates cleanly (~3.9k LOC,
~5.1k with ROI). MPR did not: `_mpr.py` imports `_progressive`,
`_working_set`, `_prefetch` and `_profile` at module level, and `_profile`
imports `_atlas`. Headless MPR therefore pulled in ~11.2k LOC, and `_mpr_qt`
~13.4k LOC.

## Current Decision

The plan was reviewed over two `/collaborative-refinement` passes with Codex
(gpt-6-astra, high effort). Both sides agreed; nothing was escalated. Adapter
scope was then settled with the user: NGFF and raw acquisition adapters stay
on this branch. Logs are
under
`.git/worktrees/napari-compositional-model/collaborative-refinement/logs/`
(`20260917-*-claude-to-codex-plan-review.md`).

### 1. Decouple on this branch first (done 2026-09-17)

The full suite stays green here while the refactor happens.

**Done.** `_mpr.py` no longer imports `_prefetch`, `_profile`, `_progressive`,
`_working_set` or `CachingSource`; `_mpr_qt.py` no longer names the atlas. The
new `_mpr_streaming.py` holds `MPRStreaming`, the `StreamingSession` it builds,
and the pane-step helpers `_source_level_steps` and `_prefetch_window`.
`attach_atlas(widget, ...)` in `_atlas_pane.py` replaces the widget's `atlas=`
keywords, using the widget's new `qt_viewers` and `add_close_callback`.
`test_core_imports.py` holds the boundary test, and it was confirmed to fail
against a planted `_profile` import in `_mpr.py`. Suite: 1453 passed, 23
skipped, over the data model, canvas, qt_viewer and slicer tests.

**Deviation from the reviewed plan.** The interaction profile went to the
streaming session rather than the core. MPR used it only for the resident
window radius, and the atlas reads it from the session, so the core needs no
`_profile` at all. That also makes the planned `atlas_layout_for` and validator
moves unnecessary: `_profile` stays with the streaming stack, importing
`_atlas` as before. Recorded here because it resolves review finding B1 by
removing the dependency rather than by relocating it.

**What the session contract is.**

1. *Prepare*, before `add_to_viewer`: the factory returns a session exposing
   its `view_source` (today the `ProgressiveSource` wrapper). If preparation
   raises, the session releases its own allocations.
2. *Start*, after the image layers exist: working-set and prefetch activity
   begins.

The controller owns the session from successful preparation onward and closes
it in `close()` and on partial construction failure.

**Tests added.** A preparation-failure case (a raising `subscribe` releases the
progressive source), a start-failure case (the controller closes the session),
a recording session that proves start/update/close ordering, and the
import-boundary test. The old construction-failure test now fails inside
`start()`, after the working set exists. `MPRStreaming` owns the prefetch and
profile validation tests that were `MPRController`'s.

**`CachingSource` extracted.** It now lives in `_caching_source.py` with the
cache-only helpers. `_normalize_region`, `_RegionBounds`, `_validate_region`
and `_validate_level` stayed in `_source.py`, because core modules
(`_basis_conversion`, `_display_sampling`, `_index_selection`) use them.

**Mixed tests split.** The cache's level-geometry and stale-selection cleanup
tests moved into `test_caching_source.py`; the acquisition half of the
core-import test moved into `test_acquisition_adapters.py` with its own
blocked-import guard. `test_profile.py` needs no split: `_profile` is an
exploration module now. The boundary test blocks `_caching_source` and
`_ngff_adapter` as well, and fails against a planted import of each.

The MPR test files needed one more pass: both imported `CachingSource` and
`MPRStreaming` at module level, so neither would have collected on the core
branch. `test_mpr_streaming.py` and `test_mpr_qt_streaming.py` now hold the
streaming halves, with `_tests/utils.py` and `_tests/conftest.py` carrying what
both halves share. See [core_manifest.md](core_manifest.md) section 4.3.

Step 1 is complete. Suite after it: 1370 passed in `src/napari/experimental`.

### 2. Module boundary

| `compositional-core` | Exploration only |
|---|---|
| `_data_object`, `_field`, `_field_geometry`, `_domain`, `_mesh_domain`, `_axis`, `_mapping`, `_graph`, `_basis_conversion`, `_validation`, `_derived`, `_mri` | `_bricks`, `_ladder`, `_downsample`, `_level_subset` |
| `_source` (without `CachingSource`), `_level_geometry` | `_caching_source`, `_working_set`, `_progressive`, `_prefetch` |
| `_index_selection`, `_selection`, `_annotation`, `_geometry`, `_shapes_codec`, `_measure` | `_atlas`, `_atlas_pane`, `_atlas_vispy` |
| `_view_bridge`, `_display_sampling`, `_image_binding`, `_layer_adapters`, `_mpr`, `_mpr_qt`, scene line/mesh overlays | `_ngff_adapter` (microscopy; imports `CachingSource`), `_profile`, `_mpr_streaming` |
| `_xarray_adapter` (DICOM path through pirana) | `_readout*`, `_mri_readout_adapter`, `_pet_event_adapter` (raw acquisition; still experimental) |

Prune `__init__.py` and `__init__.pyi` to match. Acceptance: the retained
suite collects and passes with the excluded modules physically deleted.

### 3. History

1. Tag both current tips (`explore/compositional-data-model`, `integration`)
   for rollback.
2. In a separate worktree, create `compositional-core` from `908b3ed80`,
   integration's upstream base. None of the 22 upstream commits between it and
   the merged tip `5443f3715` touch the canvas, slicer or overlay-registration
   files; three touch `pyproject.toml`, so expect a small conflict there on the
   shapely testing extra. Commits: one
   squashed core addition, then one each for the scene overlays, the canvas
   guards, the slicer fix and the `experimental/__init__` lazy import.
3. Verify the core diff file-by-file against
   [core_manifest.md](core_manifest.md) sections 1, 2 and 4.
4. Merge into `integration` with `--no-ff` so the merge reverts as one unit.
   Run the MPR tests plus the existing canvas and overlay tests there.
5. Merge (do not rebase) `compositional-core` into this branch. Resolve
   add/add conflicts in the export files and mixed tests by hand, keeping
   exploration's additional APIs. After the merge, compare the retained core
   files explicitly. Account separately for the 22 upstream commits
   (`git diff --name-only 908b3ed80 5443f3715`) and the excluded modules;
   a whole-tree diff will not show "only streaming modules".

### 4. Napari-core touches and dependencies

Nothing in this plan justifies further edits outside `experimental/_data_model`
beyond the ~80 LOC listed in Context. `shapely` stays out of napari's required
dependencies. pirana declares `shapely>=2.0`, which it needs: ROI, MPR,
`FrameRegistry` and the xarray adapter all import shapely at module level
through `_geometry`. Only the `DataObject`/`ImageBinding` entry points are
shapely-free, which is what `test_core_imports.py` checks.

### 5. Upstream

The `_layer_slicer` `tuple()` snapshot and the canvas visual guards go upstream
as two separate PRs, each with its own tracker search and reproducer. Neither
gates the split.

### 6. Architecture working group material

A dependency diagram, explicit API limits, one ROI/measurement example, and
the two-consumer ImageBinding example
([image_binding_notes.md](image_binding_notes.md)). No separate distribution.

## Alternatives Considered

- **Copy the files into `integration`.** Rejected: `_mpr.py` and `_source.py`
  are still under active change here, so two copies would diverge.
- **Cut MPR over without decoupling.** Rejected: it brings ~11-13k LOC,
  including the GPU atlas, into pirana's tree.
- **Rebase exploration onto the core branch.** Rejected: 100 commits of
  rebasing for no benefit over a merge.
- **Separate distribution for the data model.** Rejected for now: packaging
  work without removing MPR's dependence on napari internals. The code stays
  in the napari fork.
- **Base `compositional-core` on current upstream main.** Rejected: merging
  it into `integration` would also bring in 17 unrelated upstream commits.

## Deferred Work

- **Acquisition readout adapters** (`_readout*`, `_mri_readout_adapter`,
  `_pet_event_adapter`). They represent raw MRI/PET records rather than
  reconstructed volumes and are still experimental. They stay here; revisit
  once they stabilize.
- **NGFF adapter.** Microscopy; stays here.

## Next Steps

1. Build `compositional-core` per step 3, against
   [core_manifest.md](core_manifest.md), and verify it in isolation.
2. Merge into `integration`; reconcile this branch.
3. Prepare the two upstream PRs.
