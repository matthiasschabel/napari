# Shapes and slicing: where this work stands

**Status:** Active
**Last updated:** 2026-08-25
**Scope:** Entry point for the napari#9207 work and everything it pulled in. Read this first;
the detail lives in the four notes linked below.

## How we got here

We proposed freezing slice navigation while a shape is being drawn (napari#9207). brisvag
rejected that framing on napari#9439:

> As for #9207, I think this is trying to solve the wrong thing: we should rather make it so
> chaning slice does not break the in-progress shape. It's useful to move to neighboring
> slices when drawing shapes (I've wished I had this in the past), so I don't think we should
> disallow it. Rather, we should make it so you can go back to the original slice and
> continue. Or maybe even just continue on new slices, but the shape gets still drawn on the
> original one. We could signal this with some different color.

He agreed to a separate PR. Working out what that PR should be turned into a much wider
investigation, because Shapes' relationship to slicing turns out to be inconsistent in
several independent ways.

## The four artifacts

| Note | What it is |
|---|---|
| `shapes_drawing_session_design.md` | The full design and all the evidence. Converged through three codex critic passes; dispositions recorded. The long one. |
| `shapes_slice_change_proposal.md` | The short, human-readable proposal for maintainers. Also published as an artifact. |
| `shapes_projection_mode_issue_draft.md` | Draft issue: Shapes cannot participate in thick slicing. Independent of #9207. |
| `zulip_shapes_slicing_draft.md` | Draft Zulip message asking whether the inconsistencies are one piece of work or several. Not posted. |

## Settled, do not relitigate

- **The navigation lock is not the answer** and must not appear in any upstream PR.
  `feature/dims-nav-lock-draw-exempt` and `ViewerModel._on_layer_drawing_started` on
  `integration` implement the rejected approach. The per-axis padlock (napari#9442) survives
  as the *application's* explicit veto, which is a different thing.
- **Enforce the origin invariant in `ShapeList.edit`, not in the mouse bindings.** Confirmed
  twice: it is one site instead of four, and napari#9206 rewrites the creation path in a way
  that would bypass a mouse-side fix entirely while still routing through `edit`.
- **Finishing a draw on a partition change destroys work.** `_finish_drawing` discards paths
  at `<= 2` and polygons at `<= 3` stored vertices, so a two-click polygon vanishes. Measured.
- **Excluding partitions has to be enforced in code**, not asserted in prose. An unconditional
  exemption changes axis-roll behavior whether or not the design says it is out of scope.
- **Compare displayed-axis sets in layer space, not world space.** A world roll changes the
  world's displayed set while a 2D layer's own set is untouched.
- **The exempt-index lifecycle belongs to `Shapes`.** Two integer identities exist for the same
  shape (`_moving_value[0]` and the list index) and only the layer can see both.
- **No `_DrawingSession` dataclass.** It duplicated `_moving_value[0]` and the partition that
  `_ndisplay_stored` / `_display_order_stored` already hold. (If option 5 for axis rolls is
  chosen, one tuple of draw-start state comes back and *then* earns its place.)
- **The off-slice color cue is a second PR.** napari has one uniform highlight color and no
  second semantic color; the cue needs a maintainer decision on source, theme and
  accessibility first.
- **A meta/roadmap issue is the wrong vehicle.** CONTRIBUTING.md points at Zulip and the
  community meetings for conceptual discussion; the tracker is for actionable items. Ask one
  question there first.

## Open, needing a maintainer

1. **Axis roll mid-draw**: leave it, finish, cancel, refuse, or freeze-and-resume. Five options
   with costs are in the design note. Option 5 (freeze, resume when the original axes return)
   reads best and is brisvag's own principle applied to axes instead of slices.
2. **What brisvag's second clause meant.** "Continue on new slices" is ambiguous between
   anchoring, mere visibility, and multiple concurrent in-flight shapes. The readings imply
   very different amounts of work. Under suspend-and-resume it reduces to one question: what
   happens if you click while the draw is suspended on another slice?
3. **Whether the Shapes slicing inconsistencies are one piece of work.** That is what the Zulip
   draft asks.
4. **Text and thumbnail off-slice.** Both follow from the exemption; decide rather than
   discover.

## Our own conceptual shift during this session

Worth recording because it reversed twice. We started with unconditional origin anchoring
(vertices placed elsewhere land on the origin slice). The user pushed back that this is
actively dangerous for spatial axes: you would trace a boundary visible at z=5 onto z=4 with
nothing in the UI saying so. That is right, and brisvag's *primary* clause is
suspend-and-resume, not anchoring.

The proposed remedy was a per-axis "blessing" of which axes permit propagation. That is worse:
it needs napari to know axis semantics, which napari#9207 itself argued against, and `Dims.units`
cannot supply it (z in µm and wavelength in nm are both `[length]`).

**The resolution is that napari already has the mechanism, per axis, and does not need to know
what an axis means.** `projection_mode` is a base `Layer` property and thick-slice margins are
per-axis tuples on `Dims`. Verified: with the margin widened on `echo` only, Points with
`projection_mode='all'` shows every echo at one z, while an Image at `projection_mode='none'`
still shows a single echo. Shapes is the layer that cannot opt in. That is the projection-mode
issue draft, and it is independent of #9207.

## Separate defects found while verifying

Each reproducible on `upstream/main` with no part of this design.

| Defect | Destination |
|---|---|
| `Rectangle` truncates an unrounded bounding box for its slice key, so a rectangle drawn at a fractional layer coordinate is invisible on the slice it was drawn on | New issue, linked to napari#8901; distinct from our napari#9394, which fixed the picking-side tolerance |
| `ShapeList.edit(..., new_type=...)` drops `ndisplay`, raising `ValueError` on a list at `ndisplay=3`. Six-line repro, no viewer | New issue |
| `_move_selected_layer` dereferences `_selected_box` with no `None` guard, so a view change during a **SELECT-mode resize** raises `TypeError` | New issue, scoped to SELECT mode; napari#9206 removes the creation-path instance but not this one |
| Removing an unrelated **earlier** shape mid-draw raises `IndexError` | Not a new issue; add as a test on napari#9441 |
| `Shapes._reset_editable` is not wired to `ndisplay` | **Do not file.** Qt resets `editable`, so there is no user-visible consequence |
| `np.sign(0)` zeroing the rotation matrix, NaN geometry on transpose mid-drag | **Do not file.** napari#9206 removes the sign-based rotation entirely; verified fixed at that PR head |

## Others' work that matters

- **napari#9206** (psobolewskiPhD, open): rewrites `_add_rectangle_ellipse_line`. Fixes two of
  our findings and **introduces a new one** — creation now writes full-D `min`/`max`, so a
  slice change mid-drag produces a slice-spanning shape that renders nowhere. Impossible on
  `main`. That is a review comment for us to leave, not an issue.
- **napari#9302** (abhi-0203, approved, predates our #9441): competing fix for #9277. Guards
  `remove_selected` with an early return, which makes the delete key silently do nothing and
  leaves `layer.remove([earlier])` broken. Reportedly resolved by the user; check before acting.
- **napari#9435** (brisvag): renames `edge_` to `border_` across Shapes. Merge-conflict risk for
  every open Shapes PR; rebase order matters.
- **napari#5505** (MartinK84): `to_labels()` fails when shapes are drawn after changing visible
  axes. Same axis-order-mid-draw territory.
- **napari#9051 / #9059 review** (brisvag): he wants the "selected view" concept moved *out of*
  layer state. That is the disposition our state additions will be judged against.

## Next actions, in order

1. Leave the review comment on napari#9206 about the slice-spanning regression, with the
   measured output.
2. Add the earlier-index deletion test to napari#9441.
3. Rebase the four conflicting PRs (#9326, #9335, #9361, #9396); a conflicting PR reads as
   abandoned.
4. Finish napari#9442 and mark it ready. It is the padlock half of the #9207 story and two
   reviewers are engaged.
5. Post the Zulip question. One message, then stop.
6. File the three new issues, smallest first, only after the Zulip answer.
7. Then write the #9207 PR.

**The queue is the real constraint.** Fifteen open PRs, five drafts. The two newest have
maintainer engagement and the 26-day-old ones have none. Landing four or five buys more
credibility for the conceptual argument than any document will.

## Housekeeping

`per_axis_navigation_lock_design.md` is marked `Implemented` and `navigation_lock_v2_design.md`
`Deferred`, but both describe the approach brisvag turned down for #9207. Neither is wrong
about the padlock feature itself, which survives as napari#9442, so do not mark them
`Superseded` wholesale. Add a line to each pointing here and saying the draw-time lock is not
going upstream.
