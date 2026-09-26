# Production fork branch inventory

**Status:** Active
**Last updated:** 2026-09-26
**Scope:** napari integration, upstream topic branches, compositional experiments, and pirana consumers

## Context

Integration includes upstream main through 10fa02054 (#9548), which brings in the landed #9411 and #9533. A merged PR is not a reason to revert its old merge: inspect the remaining source delta. Retire a compatibility path when its fix is present in every supported consumer profile.

## Current Decision

Production assembly: `integration`. Experimental synchronization flows one way through `compositional-core` to `explore/compositional-data-model`. See [the model boundary](compositional_model_boundary.md).

Pirana viewer's stock floor is 0.9.1 (raised from 0.9.0rc1 on 2026-09-26). #9257 and #9364 are already present there, so its fork-only autorepeat probe and private Vectors mode subscription are retired. #9396 is not in stock 0.9.1: retain the explicit direct-mode resynchronization. The appended-vector-color workaround addresses a separate unresolved API gap. The independent pirana-colormap package may still support napari 0.8.

The integration keeps its layer-list held-Delete suppression for now: it prevents repeated Delete
from walking the selection and removing unrelated layers. Pirana-specific right-click vertex
rollback has been removed because Pirana owns Backspace rollback and uses right-click for its ROI
context menu.

### Open upstream PRs: retain

| PR | Branch | Purpose |
|---|---|---|
| [#9407](https://github.com/napari/napari/pull/9407) | `agent/tabular-numerals-window-scope` | Retain pending upstream review; preserve production adaptations. |
| [#9442](https://github.com/napari/napari/pull/9442) | `feature/dims-axis-lock` | Model-only per-axis lock (split 2026-09-24 at a maintainer's request). Not the owner-lock API integration ships. |
| [#9568](https://github.com/napari/napari/pull/9568) | `feature/dims-axis-lock-gui` | Padlock UI for #9442; stacked on it. |
| [#9468](https://github.com/napari/napari/pull/9468) | `feature/monospace-status-readouts` | Retain pending upstream review; preserve production adaptations. |
| [#9326](https://github.com/napari/napari/pull/9326) | `feature/playback-cycle-time` | Retain pending upstream review; preserve production adaptations. |
| [#9275](https://github.com/napari/napari/pull/9275) | `feature/shapes-drawing-state` | Retain pending upstream review; preserve production adaptations. |
| [#9361](https://github.com/napari/napari/pull/9361) | `feature/uniform-key-autorepeat` | **Generation gap.** The PR head (`73d1181fb`) is a redesign: auto-repeat by default, suppressed only for pending hold-semantics bindings, `repeatable` deprecated, `_get_repeatable_shortcuts` removed. Integration and the local branch carry the earlier opt-in-preserving generation (`b0850becc`). Decide whether integration adopts the redesign before it merges upstream; pirana's key handling sees the policy change either way. |
| [#9335](https://github.com/napari/napari/pull/9335) | `feature/vectors-feature-color-mapping-controls` | Retain pending upstream review; preserve production adaptations. |
| [#9532](https://github.com/napari/napari/pull/9532) | `fix/dock-widget-minimum-ratchet` | Retain pending upstream review; preserve production adaptations. |
| [#9462](https://github.com/napari/napari/pull/9462) | `fix/dock-widget-size-policy` | Retain pending upstream review; preserve production adaptations. |
| [#9328](https://github.com/napari/napari/pull/9328) | `fix/settings-reset-announces-every-field` | Retain pending upstream review; preserve production adaptations. |
| [#9441](https://github.com/napari/napari/pull/9441) | `fix/shapes-remove-selected-mid-draw` | Retain pending upstream review; preserve production adaptations. |
| [#9394](https://github.com/napari/napari/pull/9394) | `fix/shapes-slice-key-rounding` | Retain pending upstream review; preserve production adaptations. |
| [#9418](https://github.com/napari/napari/pull/9418) | `perf/shapes-hide-empty-subvisuals` | Retain pending upstream review; preserve production adaptations. |
| [#9419](https://github.com/napari/napari/pull/9419) | `perf/shapes-staged-creation` | Retain pending upstream review; preserve production adaptations. |
| [#9561](https://github.com/napari/napari/pull/9561) | `fix/nan-color-fast-math` | NaN `nan_color` stopgap; superseded by vispy#2796 once napari's minimum vispy includes it. |
| [#9562](https://github.com/napari/napari/pull/9562) | `fix/shapes-finish-drawing-emit-order` | Clear `_is_creating` before `_finish_drawing` emits. |
| [#9563](https://github.com/napari/napari/pull/9563) | `feature/shapes-added-event-indices` | ADDED indices counted from the end, like Points; stacked on #9562. Differs from integration, see below. |
| [#9564](https://github.com/napari/napari/pull/9564) | `fix/shapes-data-setter-scalar-shape-type` | Broadcast a scalar `shape_type` in the data setter. |
| [#9565](https://github.com/napari/napari/pull/9565) | `fix/tiled-image-keeps-state-on-retile` | Tiled image keeps settings, GL state and filters across a retile. Differs from integration, see below. |

### Upstream PRs that differ from integration

When these merge, reconcile the integration delta rather than simply dropping it:

- #9563 reports ADDED `data_indices` counted from the end (`(-2, -1)`), matching `Points.add`. Integration reports positive indices (`(2, 3)`). Pirana is indifferent: its ROI controller ignores ADDED indices and normalizes negative indices for other actions (checked 2026-09-26), so take whichever form upstream accepts.
- #9565 records settings and attached filters on `TiledImageNode` as they are assigned. Integration (8fa195756) reads settings back from the old tiles, and does not carry filters or survive an empty retile.
- #9442/#9568 add `Dims.axis_locked`, which is separate from integration's owner-lock API that pirana consumes.

### Sync check (2026-09-26)

Integration contains `upstream/main` (4b1f6dd77). Each open-PR and perf branch outside integration's history had its own changed tests run against integration. #9361 was the only feature missing; its earlier generation (`b0850becc`, opt-in model kept) is now merged. The PR as open upstream (`73d1181fb`) is a different design (repeat by default); see the table above. QA review of the merge on 2026-09-26: the `KeyBinding` coercion and identity-based dispatch check are sound, the removed `event.key is None` clause is redundant with the guard at the top of `on_key_press`, the `action_manager` -> `key_bindings` import is acyclic because `key_bindings` imports `action_manager` lazily, and `utils/_tests` + `events/_tests` + `_tests/test_key_bindings.py` pass (698 passed, 1 skipped) once the fork environment is re-synced for the new `napari-resources` dependency; pirana-gui against this tip: pytest 2611 passed, `pytest --gui` 4126 passed. The remaining failures are the known generation differences:

- ADDED indices: upstream's `test_polygons` expects `(-1,)` from `add_polygons`; integration reports `(0,)`. This fails the branches for #9275, #9563 and #9564 that carry upstream's test file.
- #9561's `decode_nan_sentinel` is superseded by integration's `decode_sentinels` (`gpu-exceptional-colors`).
- #9565, and #9442/#9568, as listed above.
- `perf/keep-extent-cache-across-slices` and `perf/mesh-shader-reuse` test a `Mesh`-based Vectors node; integration draws Vectors with `VectorsVisual`.
- `feature/dims-lock-flash`, `feature/dims-navigation-lock` and `feature/dims-nav-lock-draw-exempt` are earlier generations of integration's owner-lock API and were not retested.

`feature/direction-labels` was deleted locally and on the fork on 2026-09-26; its tip `bd501244b` is kept at `refs/archive/2026-09-26/feature/direction-labels` (local only). Branches cut from integration before 2026-09-25 (the compositional branches, `feature/gpu-exceptional-colors`, `dev/gl-exceptional-probe`, `fix/dims-lock-active-axis`, `feature/dims-lock-flash`) still carry the removed overlay until they next take integration.

### Related vispy work

- vispy#2796: a NaN test that fast-math compilers cannot fold (`gl_DepthRange.far`). Fixes napari #8056 at the source.
- vispy#2795: fixes `ImageVisual.bad_color`, which has raised since vispy#2663; `test_image_nan` needs it.
- vispy#2798 (issue): CPU-scaled NaN renders a driver-dependent color.

### Other production changes: retain

- Navigation owner locks, draw exemptions, active-axis recovery, and the `direction_edge_labels` helper remain consumed by pirana. The fork's `DirectionLabelsOverlay` and `viewer.direction_labels` were removed on 2026-09-25 (pirana never used them); the helper matches the upstream proposal on `feature/direction-edge-labels`. Open #9442 does not replace the owner-lock API; do not delete it based on that proposal.
- Shapes drawing-event order, scalar shape-type assignment, lasso vertex preservation, and custom cursors remain production features/fixes.
- Exceptional GPU colors, infinity colormap fields, NaN handling, and the selector hook remain used by pirana's colormap workflow.
- Rendering optimizations (extent/transform/shader reuse, empty subvisual suppression, viewport culling) remain separate production patches with measured evidence. No new rendering redesign is part of this maintenance pass.
- #9335 remains retained but deferred: its legacy Vectors controls do not cover the dynamic-controls path. See [the controls decision](vectors_color_mapping_controls.md). Do not invent a public Vectors API rename during cleanup.

### Archived work

[The archive manifest](branch_rationalization_archive.json) records exact branch tips, reasons, and restore refs before branch removal. Archive refs live in the primary clone, not the temporary worktrees. They are local-only; no experimental history is newly published to the public fork. Restore with `git branch <name> refs/archive/2026-09-19/<name>`.

Open PR branches and public dependency-retention tags are preserved. Merged branches are checked by landed code and regression tests; rejected prototypes are archived as unique history, never described as merged.

## Alternatives Considered

- Rebuild integration from a hand-selected manifest: rejected because it risks losing production fixes.
- Publish archive tags publicly: rejected for mixed historical/experimental content. Durable local refs retain the work without publishing it.
- Delete all branches whose PR closed: rejected; a closure can mean a still-needed downstream policy or a superseded design.

## Deferred Work

The owner-lock API remains until pirana migrates safely. The Vectors dynamic-controls gap remains an explicit upstream design task.

The held-Delete suppression remains in the integration pending a separate ROI deletion-protection
design. A future change may map Pirana's logical ROI lock to napari's `LayerLock.DELETION`, but it
must preserve `layer.editable`, define how the napari lock action synchronizes with the ROI panel,
and test layer-list notifications, selection restoration, direct controller deletion, and
undo/redo. Do not remove the global suppression as part of that work.

## Next Steps

After each upstream merge, inspect its integration delta, update the consumer compatibility boundary, run the relevant stock/fork tests, and retire the landed topic. Keep this inventory current rather than opening another competing assembly branch.
