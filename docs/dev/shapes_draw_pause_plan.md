# Shapes: pausing an in-progress draw across slice and axis changes

**Status:** Active
**Last updated:** 2026-10-06
**Scope:** napari#9207. Two upstream PRs: A (visible pause) and B (anchored editing, stacked on A).
Supersedes the PR plan in `shapes_drawing_session_design.md` ("The off-slice cue" and
"Next steps"); that note remains the evidence base.

## Context

brisvag agreed the design on napari#9439 (comment 5998535918, 2026-10-05) and asked for two PRs.
Only one shape is ever in progress per layer; there is no queue of pending shapes.

| | Clicks off the origin slice ignored | Clicks off the origin slice add vertices on the origin slice |
|---|---|---|
| Shape hidden off slice | today, roughly | drawing blind, rejected |
| Shape shown on every slice while drawing | **PR A** | **PR B** |

## Current Decision

### PR A (base `upstream/main`, "Part of #9207")

1. **Exemption.** The shape being drawn stays displayed on every slice while the draw is open.
   One private index on `ShapeList`, named for what it does (`_always_displayed_index` or
   similar, not drawing vocabulary); its lifecycle is owned by `Shapes`: set at draw start,
   cleared on finish or discard, mask recomputed after either. Upstream ends the draw after any
   removal (`shapes.py` `remove`, final `_finish_drawing()`), so removal clears the exemption.
2. **What "on the origin slice" means.** Record the view slice key at draw start and pause when
   the rounded current key differs from the rounded start key, same rounding on both sides. Do
   not compare against the shape's own `slice_key`: shape types round differently (rectangle and
   ellipse truncate), so a draw at z=0.6 or 0.5 would pause on its first move (reproduced).
3. **Pause off the origin slice.** Every draw-extension mutation is suppressed, not just clicks:
   the vertex following the cursor, automatic vertex insertion in path and lasso modes, and held
   rectangle/ellipse/line drag updates. Escape, Enter and mode change finish normally and commit
   the shape on its origin slice. (Upstream does **not** finish a draw on layer change or
   removal: `Shapes._on_selection` has no callers. That is a separate issue, not part of A.) Releasing a paused drag commits its last
   geometry from before the pause. Returning to the origin slice resumes.
4. **Pause on a displayed-axis-set change** (a roll bringing a different axis on screen),
   compared in **layer** axis space. A transpose, or a world roll seen by a lower-arity layer,
   does not pause. While paused this way the shape is explicitly hidden: the normal slice test is
   not enough, because a narrow shape can still match it after a roll (reproduced on upstream).
   Vertices are untouched; the draw resumes when the original displayed set returns. Needs one
   piece of draw-start state, the displayed set. The 2D/3D toggle already finishes the draw
   through the Qt controls; leave it alone.
5. **Off-slice cue.** While paused off its origin slice, the in-progress shape's fill and edge are
   hidden and only a dashed outline (2x highlight width) plus vertex handles is drawn, so the
   current slice shows through. Chosen 2026-10-05 over thicker or two-tone dashes, which stayed
   hard to see over the fill (mockups compared). The
   outline is a triangulated mesh built in `Shapes._outline_shapes` from cached centers and
   offsets that ignore zoom, so dashes are generated outside that cache or the cache is cleared
   on zoom. Dash length scales with `_normalized_scale_factor`. No new settings or colors.
6. **Paused feedback.** `Layer.help` status-bar text ("Drawing paused: return to the original
   slice to continue, or press Esc to finish", with an axes variant) and the `forbidden` cursor
   while paused, both restored on resume, finish, discard and mode change. Cursor and help come
   from the active layer, so layer change needs no handling.
7. **The off-slice in-progress shape is not pickable.** Hover and click hit tests must not treat
   it as present on the current slice.

### PR B (stacked on A, "Depends on #A", "Closes #9207")

**Status 2026-10-05:** draft napari#9638, commit `2dcd467d7` on `feature/shapes-draw-anchor`
(worktree `~/GitHub/napari-feat/shapes-draw-anchor`), stacked on #9636 at `ea2b27e0b`. Anchoring
applies to click-built shapes (polygon, polyline, path, lasso); rectangle/ellipse/line drags still
pause off-slice because `shift`/`transform` bypass `edit`.

Off-slice clicks are accepted. Vertices written for the exempt shape have their non-displayed
components forced to the origin's **exact** coordinates (first vertex), enforced once in
`ShapeList.edit`. Normal cursor and an "editing on slice …" hint while off-slice; a roll still
pauses. Regression test must fail on A alone.

## Tests for A

- Slice change mid-polygon: shape stays visible, data unchanged; mouse movement and clicks while
  paused leave `layer.data` unchanged; return resumes.
- Path and lasso: movement while paused inserts nothing.
- Held rectangle drag across a slice change: no `TypeError`; release commits pre-pause geometry.
- Fractional coordinate crossing a rounding boundary pauses correctly.
- Roll at viewer arity pauses and hides, including a narrow shape; transpose does not pause;
  order permuted within non-displayed axes does not pause; world roll on a 2D layer in a 4D
  viewer does not pause. Use a stack with no singleton axes (`Dims.roll()` skips `nsteps == 1`).
- Escape while rolled commits the shape on its origin plane (unverified upstream).
- Removing an earlier shape mid-draw finishes the draw and clears the exemption.
- Help text and cursor restored on every exit path.
- Dashed outline geometry, and stable dash length across zoom.

## Rectangle slice-key truncation

Draft napari#9639 (2026-10-05): `63b8a1f37` on `fix/shapes-rectangle-slice-key` (worktree
`~/GitHub/napari-feat/shapes-rect-slice-key`, off upstream `fe75ae8bf`). Only `Rectangle` truncated;
`Ellipse` truncates too but rounds its bounding box first, so it was already correct and is left
alone. Separate tiny upstream defect found: picking an ellipse exactly at its center misses (fan
triangulation vertex). Not filed.

## Porting into `integration` (pirana)

Agreed 2026-10-06. Port all four PRs (#9636-#9639) into `integration` by hand, onto its richer
creation code (`_creation_anchor`, `edit_staged`), not by merging the PR branches.

- Remove the draw-time navigation lock (`ViewerModel._on_layer_drawing_started`,
  `_draw_lock_exempt`, `_reassert_draw_lock`, the `_toggle_ndisplay` guard tied to it). Keep the
  per-axis padlock and `Dims.lock_navigation` itself, which pirana uses.
- Anchoring must cover `edit_staged` as well as `edit`.
- **Private switch** on `Shapes` choosing what click-built shapes do off-slice: extend on the
  original slice (#9638, default) or pause (#9636 behavior). Pirana sets "pause": its ROIs feed
  measurements, so a vertex placed while looking at another slice is a silent error. Raise it
  upstream on #9638 as a question; make it public only if a maintainer agrees.

Pirana follow-ups:

1. `roi_panel` overrides the viewer cursor with its add/remove cursors while a contour tool is
   armed; it must leave napari's `forbidden` cursor alone while a draw is paused.
2. Relax pirana's draw-time freeze (`freeze_navigation`, `InteractionPolicy` construction gate) so
   navigation works mid-draw, with the switch set to pause.
3. Integration tests for a slice change mid-draw: in-flight vertex history and Backspace, finish,
   leaving ROI mode.
4. Advance pirana-gui's `dev-fork` rev, pirana-lab's `NAPARI_METADATA_COMMIT` and both `uv.lock`
   together.
5. `roi_panel._discard_any_draw_in_flight` docstring describes the mode-setter order #9637 fixes.

## Alternatives Considered

- Navigation lock while drawing: rejected by the maintainer.
- Color cue: needs a settings source, theme behavior and an accessibility story; dashes need none.
- Rewriting vertices on a roll: changes the ROI's meaning in the data and cannot be undone once
  vertices are added. Rejected.
- Queue of paused shapes: too complex and fragile for the benefit.

## Deferred Work

- Downstream `integration` still carries the draw-time navigation lock
  (`ViewerModel._on_layer_drawing_started`); remove it or move it to the app once A lands.
- napari#9206 (open) rewrites the creation path; PR B must be checked against it.

## Next Steps

1. ~~Implement A~~ Done 2026-10-05 on local branch `feature/shapes-draw-pause`
   (worktree `~/GitHub/napari-feat/shapes-draw-pause`, commits `8b2e8c300` + `9868b3eec` on
   `e97f57660`). gpt-6-astra implemented in two rounds, Opus 5.5 reviewed to Accept; 922 tests
   pass. Dashed outline checked by forced offscreen render: constant screen-space dash length
   at 1x/3x/8x, clean corners. Not yet pushed or opened.
2. A opened as draft napari#9636 on 2026-10-05 (squashed to `57719a2bf`; pre-squash tip
   `de39311d1`). Then B on top.
3. Separate small PR, draft napari#9637 (2026-10-05): branch `fix/shapes-mode-change-finish`
   (`80cb678e7`, worktree `~/GitHub/napari-feat/shapes-mode-change-finish`). Switching mode
   mid-draw kept the cursor vertex on polygons and polylines. Finishing first exposed
   `ShapeList.edit(new_type=...)` dropping `ndisplay` (ValueError on the 3D toggle mid-draw), fixed
   in the same PR. Conflicts trivially with A in the mode setter; whichever lands second rebases.
4. Note for B: with a half-integer data point (layer scale 2), world 0 and 1 round to the same
   plane, so A does not pause there; B's exact-origin clamp covers it.

Plan review: Codex gpt-6-astra, 2026-10-05, log
`.git/collaborative-refinement/logs/20261005-095939-828629-claude-to-codex-plan-review.md`.
