# In-progress Shapes state across slice changes

**Status:** Active
**Last updated:** 2026-08-25
**Scope:** `Shapes` creation interaction, `ShapeList` slice filtering, the VisPy Shapes adapter. Supersedes the navigation-lock approach to napari#9207 for the *default* behavior.

Refined against a codex critic pass (log:
`.git/collaborative-refinement/logs/20260825-085022-051948-claude-to-codex-plan-review.md`,
report: `/private/tmp/claude-501/-Users-matthiasschabel-GitHub-napari/e127b781-b5cd-48d0-87bb-a2fc433493f1/scratchpad/codex-review-1.md`).
Every claim below was re-verified against `upstream/main` at `c9c52267` with headless
`ViewerModel` probes. Where a claim was wrong in the first draft it is corrected in place
and noted.

## Context

napari#9207 reports that stepping a slider mid-draw silently orphans the in-progress
shape. Our first answer was to freeze slice navigation for the duration of the draw.
brisvag rejected that framing on napari#9439:

> As for #9207, I think this is trying to solve the wrong thing: we should rather make it
> so changing slice does *not* break the in-progress shape. It's useful to move to
> neighboring slices when drawing shapes (I've wished I had this in the past), so I don't
> think we should disallow it. Rather, we should make it so you can go back to the
> original slice and continue. Or maybe even just continue on new slices, but the shape
> gets still drawn on the original one. We could signal this with some different color.

He is right, and he agreed to a separate PR for it.

Starting point:

- napari#9059 (merged) keeps `selected_data` across a reslice. The remaining damage is
  geometric and visual.
- napari#9275 (`Shapes.is_creating` plus `drawing_started`/`drawing_finished`) has **not**
  landed. Upstream has only a private `_is_creating` bool (`shapes.py:577`). This design
  does not depend on #9275; do not cite those symbols in the PR rationale.
- napari#9442 (per-axis padlock) is *not* superseded. It is the application's explicit
  veto on one axis. This note is about what napari does when nothing vetoes.

## Scope decision

**This design covers slice-*point* changes only.** Partition changes (`Dims.order`,
`Dims.ndisplay`) are excluded, and the exclusion is enforced in code by suspending both
invariants when the displayed axis set changes, not merely asserted here. See "Partition
changes" below. Slice-point behavior is the whole of what brisvag asked for and where all
the user value is.

## What actually breaks

Verified on `upstream/main` `c9c52267`, headless `ViewerModel`, 4D image `(t, z, y, x)`,
4D Shapes layer, `add_polygon` open at `z = 4`.

| # | Symptom | Mechanism |
|---|---|---|
| S1 | The shape becomes invisible on **every** slice, permanently | A vertex placed after the slice change carries the new out-of-plane coordinate, so `Shape.slice_key` widens to `[[0,4],[0,5]]`. `ShapeList._update_displayed` requires *both* the lower and upper key within half a slice of the view key, so a spanning shape matches nowhere. |
| S2 | The shape disappears while off its origin slice | `_displayed` is purely slice-derived. Nothing marks the shape under construction as an exception. |
| S3 | Interaction box, vertex handles and outline disappear off-slice | `_selected_data_in_view` filters by `_view_indices`, which is `np.flatnonzero(_displayed)`. Hit testing (`ShapeList.inside`, `shapes_in_box`) filters separately through `_visible_shapes`. |
| S4 | **A slice change during a held drag raises** in rectangle, ellipse and line creation | `_set_view_slice` recomputes `_selected_box` from `_selected_data_in_view`, which is empty off-slice, so `_selected_box` becomes `None`. `_add_rectangle_ellipse_line` then indexes it unconditionally (`box = layer._selected_box; box[Box.HANDLE]`). Reachable while the button is held: `increment_dims_left`/`right` are registered viewer actions on the arrow keys (`_viewer_key_bindings.py:94-105`), and `dims_scroll` steps the slice on Ctrl+scroll; neither is gated on drawing state. |
| S5 | A rectangle drawn on a fractional slice **vanishes the moment it is drawn** | `Rectangle._update_displayed_data` computes `slice_key` by `.astype(int)` on an *unrounded* bounding box, which truncates toward zero, while `Polygon`/`Path` use `np.rint` and `Line` uses `np.round`. Independent of any draw; see below. |

S4 measured:

```
after first drag move: selected_box is None? False
after slice change:    selected_box is None? True
next drag move RAISED: TypeError 'NoneType' object is not subscriptable
```

S5 measured, with an ordinary `translate=(0.4, 0, 0)` on the Shapes layer so world slider
steps land on fractional layer coordinates (world step 5 becomes data `z = 4.60`). A
rectangle and a polygon drawn at that same coordinate:

```
data z=4.6:  Rectangle.slice_key=[4]   Polygon.slice_key=[5]
viewing data z=4.6:  displayed(rect, poly) = [0, 1]
```

The rectangle is invisible **on the very slice it was drawn on**, while the polygon is
fine. This needs no in-progress shape and no slice change, so it is a standalone defect
rather than part of this design; it is listed under separate issues.

**Corrections from the first draft.** Two claims did not survive verification:

- `Ellipse` is **not** affected by S5. It rounds its bounding box at construction
  (`ellipse.py:74-80`) before the integer cast, so it keys to 5 at 4.6 like the others.
  Only `Rectangle` truncates. Our own probe output said so and the prose said otherwise.
- A claimed sixth item, "`Shapes.editable` is not recomputed on an `ndisplay` change", is
  true at model level (`_reset_editable` is called only from the `data` setter;
  `Layer.__init__` sets `_editable = True` directly) but has **no user-visible effect**:
  `QtShapesControls._on_ndisplay_changed` sets `layer.editable = (ndisplay == 2)`
  (`qt_shapes_controls.py:240-242`), which cascades through `_on_editable_changed` to
  `mode = PAN_ZOOM` to `_finish_drawing`. So 2D/3D toggles already finish a draw in the
  real GUI. It is a model/GUI split worth its own issue, not a finding here.

Two things that are **already correct** and should not be touched:

- A pure round trip with no vertex placed off-slice restores the shape exactly
  (`displayed` goes 1 → 0 → 1). brisvag's "go back and continue" is one bug away.
- A Shapes layer with fewer dims than the viewer is unaffected by sliders it does not
  consume. A 2D layer in a 4D viewer keeps drawing through slider moves.

**The narrowing below holds on `main` but does not survive napari#9206.** That PR
(psobolewskiPhD, open, mergeable) rewrites `_add_rectangle_ellipse_line` to build the shape
from a full-dimensional `np.minimum`/`np.maximum` of the press point and the cursor, so a
slice change mid-drag writes two different out-of-plane values. Measured at PR head
`cb8a9556`:

```
before:                        slice_key = [4, 4]  displayed = 1
after a slice change mid-drag: slice_key = [4, 5]  displayed = 0
z column of data = [4.0, 4.0, 5.0, 4.0]     <- spans, so invisible everywhere
```

That corruption is impossible on current `main`, so it is worth raising on #9206. It also
*strengthens* this design: #9206 routes creation through `layer._data_view.edit(index, data)`,
so the origin projection proposed here covers it for free. Enforcing in `ShapeList.edit`
rather than in the mouse bindings is what makes the fix survive that refactor.

With that caveat, on `main`: **`add_rectangle`, `add_ellipse` and
`add_line` cannot be geometrically corrupted mid-drag.** Their drag path goes through
`ShapeList.shift`/`transform`, which only index `dims_displayed`. Only the initial press
writes full-dimensional coordinates. The polygon family (`add_polygon`,
`add_polygon_lasso`, `add_path`, `add_polyline`) writes full coordinates on every mouse
*move*, so those corrupt on the first cursor motion after a slice change, not on the next
click.

## Root cause

The shape under construction is the same kind of object as a committed shape: an entry in
`ShapeList.shapes` whose visibility is decided by comparing a `slice_key` derived from its
own vertices against the view's slice key. Every symptom follows from that one conflation:

- visibility is slice-derived, so it vanishes off-slice (S2, S3), and its interaction box
  goes with it (S4);
- `slice_key` is derived from `data`, so a stray vertex silently re-keys it (S1);
- nothing records which slice the draw belongs to, so no code can anchor to it.

## Design

Two invariants.

**I1. A draw has an origin slice, and every vertex lands on it.** The origin is
`shapes[exempt].data[0][not_displayed]`, in layer data coordinates. No stored state:
vertex 0 is written once at draw start and is never rewritten during creation (the
`vertices[0] = coordinates` branch in `_move_active_element_under_cursor` is `Mode.DIRECT`
only). Deriving beats caching: no invalidation, no staleness, and it is exact where
`Shape.slice_key` has already been rounded to an integer.

**I2. The shape under construction is exempt from slice filtering.** One exempt-shape index on `ShapeList`, honored in `_update_displayed`, its
lifecycle owned by `Shapes` (see below).

I2 is what fixes S4, so it is a correctness fix and not only a visual one.

I2 does **not** extend to `_visible_shapes`, and therefore not to `inside()` or
`shapes_in_box()`. The first draft proposed it for filter consistency, but no creation path
demonstrably needs it: rendering, handles and outline all go through `_displayed`; polygon
closing is by double-click, not hit test; and `add_vertex_to_path` falls back to
`_moving_value` when hit testing returns no vertex. The two filters also differ
deliberately for spanning shapes (`_shape_list.py:749-774,1859-1871`), so "make them
agree" is not the clean invariant it looked like. Leave it out and add a test recording
that the off-slice in-progress shape is not pickable, so the divergence reads as deliberate
rather than as drift.

### Where to enforce I1

In `ShapeList.edit`, projecting onto the origin plane when `index` is the exempt shape. Every
polygon-family creation write reaches it (`_shapes_mouse_bindings.py:397,964`;
`shapes.py:2743,2769`), so one site replaces four. Be precise about coverage: rect,
ellipse and line creation goes through `shift`/`transform` instead, which never touch
non-displayed axes and so need no projection.

The "it also protects programmatic drivers" argument in the first draft is weak, since no
public API establishes the active state. The real argument is single-site enforcement.

`_finish_drawing`'s trailing-vertex trims and the lasso RDP pass are safe under the
projection: `rdp` returns a subset of the original rows (`_shapes_utils.py:1375`), and the
trims slice, so the origin coordinate survives both.

### No `_DrawingSession`

The first draft proposed a dataclass holding `index` and the displayed-axis tuple. It does
not earn its place. `index` duplicates `_moving_value[0]` and the new exempt index, and
the partition is already stored as `_ndisplay_stored` and `_display_order_stored` in
`_set_view_slice` (`shapes.py:2343-2344,2419-2428`). Since the design deliberately leaves
the rest of the drawing state alone, the dataclass would own nothing. Use the exempt index
plus the existing stored partition.

### The exemption index is owned by `Shapes`, not by `ShapeList`

Writing the index raw is not enough, because the filter it feeds is a snapshot. `_displayed`
is recomputed only inside `_update_displayed` (`_shape_list.py:749-774`), so clearing the
exemption without recomputing leaves the just-committed shape displayed on a slice it does
not belong to. Measured, finishing while off-origin with no intervening reslice:

```
case 1: rectangle finished off-origin   -> displayed=1  view_indices=[0]   STALE
case 2: polygon, index cleared last     -> displayed=1  view_indices=[0]   STALE
```

Case 1 happens because `_finish_drawing` performs no final `edit` for rectangle, ellipse or
line, so nothing triggers a recompute. Case 2 happens for every shape type once the clear
sits where it naturally belongs, after `_finish_drawing` rather than before. The polygon
path only appeared to work in the prototype because its trailing-vertex `edit` incidentally
recomputed between the clear and the check.

The first draft put the lifecycle in `ShapeList.remove_multiple`. That is the wrong owner.
There are two integer identities for the same shape, `_moving_value[0]` on the layer and the
exemption index on the list, and `ShapeList` can renumber its own but cannot touch the
layer's. Splitting one lifecycle across two objects that can each only fix half of it is
exactly the kind of thing a reviewer should reject.

So: **`Shapes` owns the transition.** It already owns `_moving_value`, `_is_creating` and
every call to `_finish_drawing`, so it is the only place both identities are visible. One
private method sets the index at draw start and clears it on finish or discard, recomputing
`_displayed` after the write. `ShapeList` keeps only a defensive clamp, because it mutates
its own list independently and an out-of-range index would otherwise raise inside
`_update_displayed`.

Note that `_visible_shapes` needs **no** invalidation here. The first draft said the
transition must invalidate it, which contradicts the decision above that the exemption does
not extend to it. It depends only on the slice key and shape geometry
(`_shape_list.py:1859-1871`), both of which already clear the cache on mutation. Recompute
`_displayed`; leave picking caches to the mutations that actually affect them.

**Deletion mid-draw is already a known upstream defect and is not this PR's to fix.**
Removing *any* shape while a draw is open raises on upstream, including an unrelated earlier
one:

```
3 shapes, drawing the third, then remove([0]):
IndexError: list index out of range   (shapes.py:2745, from _finish_drawing)
```

napari#9277 reports the `remove_selected()` form and our open napari#9441 fixes it by
clamping an out-of-range index and cancelling the draw. Because GUI creation always appends,
the active shape is last, so removing an earlier shape always pushes the index out of range
and #9441's clamp catches that case too. Two consequences: this PR should not restate the
fix, and **the earlier-index trigger deserves a test in #9441**, which currently only
exercises `remove_selected`.

### Behavior matrix

| Mid-draw change | Proposal |
|---|---|
| Slice point moves on an axis the layer slices | Draw stays open. Vertices anchor to the origin slice (I1). Shape stays drawn (I2), styled as off-origin. Returning restores normal styling. Interaction box survives, so S4 stops crashing. |
| Slice point moves on an axis the layer does not slice | Nothing. Already correct. |
| `order` or `ndisplay` change that keeps the displayed axis *set* (a `transpose`, or a world roll seen by a lower-arity layer) | Anchor and exemption stay valid; a click-based draw continues. A *drag* in flight is separately broken upstream and this PR does not claim to fix it. |
| `order` or `ndisplay` change that alters the displayed axis set | Exemption and projection suspend for the rest of the draw, so upstream handling resumes *from the partition change onward*. |
| Layer removed, `data` set, mode change, deselect | Unchanged; already finishes. |

### The off-slice cue: a follow-up PR, not this one

brisvag asked for "some different color", which is a sketch, not a specification. Upstream
has exactly one configurable highlight color and the adapter applies it uniformly
(`_vispy/layers/shapes.py:84-104`); `settings/_appearance.py` exposes no second semantic
color. So "swap the color" still needs someone to choose a derivation, a light/dark-theme
behavior, and an accessibility story, none of which the maintainer has stated.

That is a UX decision attached to a bug fix, and it will attract opinions. Ship the model
fix first and follow with the cue once brisvag names the semantic source. Offer the split
in the issue rather than deciding it unilaterally, since he asked for both together.

The mechanism, when it does get written, is cheap: during a draw the in-progress shape is
the only selected one, so `_outline_shapes` returns only its outline and one conditional on
the uniform highlight color is the whole change. Two approaches already rejected:

- Mutating `_mesh.triangles_colors` for the active shape's range to desaturate the fill. It
  is the more legible cue, but it makes a derived cache disagree with the layer's color
  arrays for the duration of the draw.
- A `Shapes.draw_off_slice_color` public attribute. New API for a transient state, and it
  would have to interact with the color-cycle and colormap machinery for no gain.

"On the origin slice" for styling must use the same half-slice tolerance as
`_update_displayed`, not equality. A layer with `scale=(2, 1, 1)` moves half a data unit per
slider step, so equality would misreport an on-slice draw.

## Partition changes: suspend, and enforce the scope in code

The first draft proposed finishing the draw on an `order` or `ndisplay` change, on the
grounds that it is strictly better than orphaning. Verification shows that is false, and
that the case is smaller than it looked.

### Rolling and permutation, measured

`Dims.roll()` (Ctrl+E) and `Dims.transpose()` (Ctrl+T) are keybindings, so both are
reachable mid-draw and mid-*drag*. What a Shapes layer actually sees depends on its arity
relative to the world, which is easy to get wrong by reasoning about world axes:

| Case | world `order` | layer `order` | layer `displayed` | in-progress shape |
|---|---|---|---|---|
| 4D layer, `roll()` | (0,1,2,3) → (3,0,1,2) | same | (2,3) → (1,2) | hidden |
| **2D layer in 4D world, `roll()`** | (0,1,2,3) → (3,0,1,2) | (0,1) → **(1,0)** | (0,1) → (1,0) | **stays visible** |
| 3D layer in 4D world, `roll()` | (0,1,2,3) → (3,0,1,2) | (0,1,2) → (2,0,1) | (1,2) → (0,1) | hidden |
| 4D layer, `transpose()` | (0,1,2,3) → (0,1,3,2) | same | (2,3) → (3,2) | stays visible |
| permute non-displayed only | (0,1,2,3) → (1,0,2,3) | same | (2,3) unchanged | stays visible |

Three things follow.

**The displayed-set comparison must be in layer axis space.** A world roll changes the
world displayed set from {2,3} to {1,2} while a 2D layer's own set stays {0,1}. Comparing
world sets would suspend a 2D layer that is entirely unaffected. Compare
`self._slice_input.displayed`, which is layer-local, against `_display_order_stored`.

**A 2D layer experiences a world roll as a transpose of its own axes**, and a genuine
`transpose()` is a permutation within the displayed set. In both, the shape's `data` is
untouched, `slice_key` is unchanged, and the anchor still names the same plane, because
`not_displayed` is compared as a set of axis indices rather than positionally.

**Permuting only non-displayed axes re-keys both sides together.** `Shape.slice_key`
becomes `[4, 0]` and `ShapeList.slice_key` becomes `[4.0, 0.0]`, so `_displayed` stays
correct. No special handling needed.

One trap worth recording: `Dims.roll()` only rolls axes that are `rollable` **and** have
`nsteps > 1`. With a singleton leading axis, (0,1,2,3) rolls to (0,3,1,2) rather than
(3,0,1,2). The displayed set still changes, so the rule is unaffected, but any test that
hard-codes the post-roll order needs a non-singleton stack.

### Partition changes during a held drag are broken upstream, both ways

This is the part of the roll interaction that most deserves care, and it is where the
first draft would have blessed a corrupt path.

Both are reachable from the keyboard while a mouse button is held: `roll_axes` and
`transpose_axes` are registered viewer actions (`_viewer_key_bindings.py:118-131`), and Qt
delivers key events to the focused widget whether or not a drag is in progress.

**A roll mid-drag raises**, the same `TypeError` as S4 and by the same mechanism: an order
change sets `view_changed`, `_selected_data_in_view` is empty once the shape is off-display,
`_selected_box` becomes `None`, and `_add_rectangle_ellipse_line` indexes it. So S4 has two
triggers, and the exemption only removes one of them.

**A transpose mid-drag silently corrupts geometry to NaN**, which is worse because nothing
raises:

```
transpose mid rect-drag, corners before: [[10,10],[10,18],[20,18],[20,10]]
                         corners after : [[nan,nan],[nan,nan],[nan,nan],[nan,nan]]
```

The cause is not stale drag state, which was the obvious hypothesis and is wrong:
re-deriving `_fixed_vertex` from the recomputed box does not help. It is an unguarded
`np.sign` in `_add_rectangle_ellipse_line`. Before the transpose,
`box[Box.HANDLE] - box[Box.CENTER]` is `[0, -24]`, so `sign = np.sign(-1) = -1`. After, it
is `[-24, 0]`, so `sign = np.sign(0) = 0`, the rotation matrix becomes all zeros, and
`drag_scale` divides by zero. The function already guards the *norm* of `handle_offset`
being zero; it does not guard a zero *component*.

Consequences for this design:

- **Do not claim drag-based creation survives a transpose.** The displayed-set rule is the
  right test for the *anchor*, but it is not a statement about the drag machinery, which
  carries displayed-space state (`_fixed_vertex`, `_drag_start`, `_selected_box`) and an
  orientation assumption that a permutation breaks.
- **Both defects are pre-existing and out of scope**, and the suspension rule preserves
  them exactly rather than making them worse. They are filed separately below. The PR
  should say plainly that it fixes S4 for slice-*point* navigation and that partition
  changes have their own open defects, so a reviewer who tries Ctrl+E mid-drag is not
  surprised.
- A useful asymmetry for whoever picks up the partition policy: `_finish_drawing` is
  destructive only for the polygon family below its minimum vertex count (path at `<= 2`,
  polygon at `<= 3`). Rectangle, ellipse and line always hold four vertices, so ending
  those drags by finishing always commits. Measured.

### Excluding partitions is a code obligation, not a sentence in a design note

An unconditional exemption changes axis-roll behavior whether or not the note says
partitions are out of scope. `_set_view_slice` rewrites every shape's `dims_order` on an
`order` change (`shapes.py:2426-2428`), and `ShapeList.update_dims_order` rebuilds geometry
and displayed state immediately (`_shape_list.py:1578-1591`). Measured, rolling a displayed
axis out of the displayed set mid-draw:

```
exempt=False   displayed [2,3] -> [1,3]   shape displayed=0     (upstream behavior)
exempt=True    displayed [2,3] -> [1,3]   shape displayed=1     (silently changed)
```

Projection is affected the same way: after the roll, `data[0][not_displayed]` names a
different plane, so `edit` would project new vertices against axes the draw never chose.

So the PR must **suspend** both invariants when the displayed axis *set* changes. This is
not a rollback: vertices already projected during an earlier slice-point change keep their
anchored coordinates, so a sequence of slice move, vertex placement, then roll still differs
from upstream. What suspension guarantees is narrower and worth stating that way: the new
invariants are never applied under a partition the draw did not start in, and handling from
the partition change onward is upstream's. Specifics:

- Detect before the `_data_view` reconfiguration batch in `_set_view_slice`. Compare
  `set(self._slice_input.displayed)` against the set implied by `_display_order_stored` and
  `_ndisplay_stored`, which at that moment still hold the pre-change partition. No new
  state, so the "no `_DrawingSession`" conclusion survives.
- Compare the *set*, not the order tuple, and compare it in **layer** axis space. A
  permutation within the displayed set, or within the non-displayed set, leaves the anchor
  meaningful and must not suspend anything. Layer space matters: a world roll changes the
  world displayed set while leaving a 2D layer's own set untouched.
- Suspension is one-way for the rest of the draw. Re-arming on a return to the original
  partition would need draw-start state, and the draw has already been allowed to place
  vertices under upstream semantics by then, so re-arming would be anchoring to a plane the
  shape may no longer sit on.
- Suspended is the exempt index being unset while a draw is open. That is upstream behavior
  restored, not a third state needing its own representation.

1. **Finishing destroys work.** `_finish_drawing` removes paths with at most two vertices
   and polygons with at most three stored vertices. A two-click polygon is silently
   deleted:

   ```
   nshapes before finish: 1  vertices: 3
   nshapes after finish : 0  <- work destroyed
   ```

2. **`ndisplay` is already handled.** The Qt controls path above already finishes the draw
   on a 2D/3D toggle, discard included. Adding a model-level policy would duplicate it.

3. **Finishing after `ShapeList` is reconfigured raises**, though not by the route first
   assumed. `ShapeList.edit(..., new_type=...)` propagates `dims_order` to the replacement
   shape but not `ndisplay` (`_shape_list.py:1461-1466`), so on a list at `ndisplay = 3` it
   gives `ValueError: could not broadcast input array from shape (4,2) into shape (4,3)`.
   That is an API defect reproducible in six lines with no viewer and no drawing, and it is
   filed as such below rather than dressed up as a drawing bug.

   The GUI 2D/3D button does **not** reach it, and the reason is worth recording because it
   is not obvious. `ViewerModel._update_layers` is connected to `dims.events.ndisplay`
   before the Qt controls are (`viewer_model.py:269` at model construction versus
   `qt_layer_controls_container.py:103` at Qt construction), so the layer reconfigures to
   `ndisplay = 3` *first*. But the Qt reset then goes `editable = False` →
   `_on_editable_changed` → `mode = PAN_ZOOM`, and the mode setter assigns `self._mode`
   **before** calling `_finish_drawing`. By then the mode is no longer a polygon or path
   add mode, so the `new_type` conversion branch never fires and the draw commits cleanly
   (`nshapes` 1 → 1, `is_creating` False). Setting `viewer.dims.ndisplay = 3` from a script
   with no Qt controls attached leaves the draw open at `mode='add_polygon'`, and a
   subsequent Escape does raise. Say that precisely; do not claim a GUI crash.

4. **Finishing mid-drag leaves the generator in a broken state**, but this is
   pre-existing, not introduced. `_finish_drawing` clears `_moving_value`, and the next
   drag update hits `assert vertex is not None`. Escape mid-drag on upstream today
   produces the identical `AssertionError`, so it is an existing defect in the drag
   generators' lifecycle, not a consequence of any partition policy. Worth its own issue.

So the only genuinely unhandled partition case is an **`order` change (axis roll)**
mid-draw, which today orphans the shape exactly as a slice change does, and which the
suspension rule above preserves unchanged. Ask brisvag rather than deciding it here, and
put five options in front of him rather than three.

### The five options for an axis roll mid-draw

| | Option | Cost |
|---|---|---|
| 1 | Keep orphaning (status quo, what this PR preserves) | Shape vanishes, draw silently continues onto the new plane. Bad, but destroys nothing. |
| 2 | Finish the draw | Destroys a path at `<= 2` or a polygon at `<= 3` stored vertices. Safe for rectangle/ellipse/line, which always hold four. |
| 3 | Cancel the draw | Destructive by definition. |
| 4 | Refuse the roll while a draw is open and the displayed set would change | Non-destructive, but no clean enforcement point; see below. |
| 5 | Freeze the draw: suspend, and refuse to extend the shape until the original displayed set returns | Non-destructive; the draw resumes on rolling back. |

**Option 4, refusing the roll.** There is an existing upstream hook and it is worth knowing
about: `Dims.rollable` is a per-axis tuple, user-settable from the axis-list checkboxes, and
`roll()` already respects it. Marking the *displayed* axes non-rollable does not block the
roll, it degrades it to a permutation of the non-displayed axes only:

```
rollable=(T,T,T,T)     order (0,1,2,3) -> (3,0,1,2)   displayed (2,3) -> (1,2)   unsafe
rollable=(T,T,F,F)     order (0,1,2,3) -> (1,0,2,3)   displayed (2,3) unchanged  safe
rollable=(F,F,F,F)     order unchanged                                            no-op, silent
```

That is elegant, and it is exactly the safe case this design already allows. But `rollable`
is a *preference consulted by `roll()`*, not an enforcement mechanism. Measured, with all
axes non-rollable: `transpose()` still swaps, `dims.order = (3,2,1,0)` still applies, and
`ndisplay = 3` still repartitions. The axis-list drag-reorder is a fourth uncovered route.

So refusing needs either a guard on `Dims` itself, which would make `Dims` ask whether some
layer is mid-draw and is a layering inversion, or interception of just the two keybindings,
which leaks through the other three routes. And driving it by mutating `rollable` would have
a draw silently flipping user-visible checkboxes, leaving them wrong if the draw is
abandoned. None of that is clean enough to propose as *our* recommendation, though it is
firmly the maintainers' call whether a `Dims`-level guard is acceptable in their codebase.

Framing matters here: brisvag rejected blocking *slice* navigation, and his reason was that
moving to neighboring slices mid-draw is useful. There is no analogous use for rolling axes
mid-draw, since the plane the geometry lives in is the thing being changed. The objection
plausibly does not transfer, but say so explicitly rather than appearing to re-propose the
lock he already turned down.

**Option 5, freezing the draw**, is the one to lead with, because it is brisvag's own stated
principle applied to partitions instead of slices: "make it so you can go back to the
original slice and continue." Suspend the exemption so the shape hides exactly as upstream
does, and additionally refuse to extend it until the displayed set returns to what it was at
draw start. Measured, the round trip works and costs nothing:

```
draw-start layer displayed set: {2, 3}
  roll x1  displayed=[1,2]  shape_displayed=0  data intact=True
  roll x2  displayed=[0,1]  shape_displayed=0  data intact=True
  roll x3  displayed=[0,3]  shape_displayed=0  data intact=True
  roll x4  displayed=[2,3]  shape_displayed=1  data intact=True   <- resumes
```

Nothing is destroyed, no navigation is blocked, no layering is inverted, and the vertices are
untouched throughout. The cost is one tuple of draw-start state, the displayed set, which is
the field deleted with `_DrawingSession` in pass 2. It would now be earning its place rather
than duplicating `_display_order_stored`, because "the partition this draw started in" is
genuinely not recoverable from the last-slice snapshot. Say that explicitly if it is
reintroduced, so it does not look like a reversal.

## "Propagate across some axes but not others" is `projection_mode`, not a new concept

An ROI that should follow a parametric axis (fine-tune one contour against several echo
times) but *not* a spatial one is not a drawing problem, and it does not need napari to learn
which axes are spatial. napari already has the concept, it is already per-axis, and Shapes is
the layer that never got it.

`projection_mode` is a base `Layer` property (`base.py:373-375, 511, 808`) documented as how
out-of-slice data "should be projected onto the viewed dimensions". Per-axis granularity comes
from the thick-slice margins on `Dims`, which are per-axis tuples. Verified on `upstream/main`
with Points in a `(echo, z, y, x)` viewer:

```
default (projection none, no margins)             points in view = 1
projection_mode='all', still no margins           points in view = 1
margins widened on the echo axis only             points in view = 3   <- all echoes, one z
```

Shapes accepts `projection_mode` in its constructor and hands it to `Layer.__init__`, but
never declares a `_projectionclass`, so it inherits `BaseProjectionMode`, whose only member is
`NONE`:

```
Shapes projection_mode = 'none'
setting 'all' -> ValueError: 'all' is not a valid BaseProjectionMode
```

`ShapeList.slice_key` reads only `_data_slice.point` and ignores margins entirely, so thick
slicing has no effect on Shapes at all. Points, Vectors and Image all have real projection
classes; Shapes is the gap.

So the request to make upstream is **"Shapes should support `projection_mode`, honoring the
margins `Dims` already provides"** — existing vocabulary, existing API, existing GUI (thick
slicing is already reachable by right-clicking a slider), existing precedent in three other
layers, and per-axis for free. That is a far easier sell than a new per-axis "blessing" tuple,
and it does not require napari to know what an echo is.

It is also **orthogonal to napari#9207**. It is a slicing and rendering property, not a
drawing one. Its effect on drawing is a happy side effect: with the parametric axis thick and
the layer projecting, a contour drawn at one echo stays visible while scrolling echoes and can
be edited there, with no anchoring, no blessing and no suspension for that axis. The spatial
axis, with zero margin, still suspends. **The two cases separate by data rather than by
policy**, which is the whole reason this framing is better.

The core of the change looks small: `_update_displayed` compares `abs(slice_keys - slice_key)
< 0.5`, and a margin-aware version compares against the `[point - margin_left, point +
margin_right]` window instead. `_visible_shapes` already uses containment, so picking a shape
that spans a range works today (napari#7459) even though rendering it does not — which is the
same inconsistency from the other side, and evidence the span concept is already half-built.

Sizing and design for that belong in their own note; it is a separate PR from #9207 and
probably its own issue first.

## Alternatives considered

- **Move the in-progress shape out of `ShapeList` into a separate holder.** Conceptually
  cleanest: it is not data yet. Rejected for the same reason `shapes_active_edit_overlay.md`
  rejected it: `layer.data`, `nshapes`, `selected_data` and the `ADDING` → `ADDED` sequence
  all go live at the first vertex today, and plugins depend on that.
- **A scene `Overlay` for the active shape.** Same rejection as the perf note: leaks into
  layer selection, events, serialization and grid layout.
- **Route this through the staged-creation path** (ours, unlanded; not a PR-facing
  alternative). That path already renders the in-progress shape separately, but staging is
  gated on `ndim == ndisplay`, which is precisely the case where slices do not exist. It
  cannot be the vehicle without hideable committed ranges first.
- **Keep the navigation lock as the default.** What brisvag rejected. Retain it only as
  the opt-in per-axis padlock (napari#9442).
- **Store the anchor as a `dict[int, float]` captured at the first vertex.** Works, but
  adds state to invalidate, and `shapes[exempt].data[0]` already holds the number.
- **Enforce I1 in the mouse bindings.** Four sites instead of one, and it misses any
  future creation path.

## Prototype

`/private/tmp/claude-501/-Users-matthiasschabel-GitHub-napari/e127b781-b5cd-48d0-87bb-a2fc433493f1/scratchpad/prototype_9207.diff`:
an exempt index on `ShapeList` honored in `_update_displayed`, plus origin projection in two
mouse handlers (the prototype uses the mouse-side placement the final design rejects; the
observed behavior is the same either way). About 20 lines. Measured on upstream/main:

```
z=4 (origin)                displayed=1  shape.slice_key=[4,4]  sel_in_view=[0]
z=5, off origin             displayed=1  shape.slice_key=[4,4]  sel_in_view=[0]
after vertex placed on z=5  displayed=1  shape.slice_key=[4,4]  sel_in_view=[0]
back on z=4                 displayed=1  shape.slice_key=[4,4]  sel_in_view=[0]
after finish                displayed=1  shape.slice_key=[4,4]
committed, viewed from z=5  displayed=0
```

The same patch turns S4's `TypeError` into `next drag move OK`.

The exemption also holds with committed shapes spread over several slices, which makes the
displayed index runs non-contiguous. Mesh triangle indices stayed in bounds at every
slice. That is the failure mode recorded in `napari-shapes-staged-creation-ndim-gate`, and
it does not recur here because the active shape has real geometry in the aggregate arrays
rather than a zero-width range.

The prototype does not patch `_visible_shapes`, and `inside()` correspondingly returns
`None` over the off-slice active shape.

## Known consequences

- **The off-slice in-progress shape is not pickable.** `_visible_shapes` is deliberately
  left alone (see I2), so `inside()` and `shapes_in_box()` do not see it. No creation path
  needs them to, but it is a real asymmetry with `_displayed` and belongs in a test rather
  than in a reader's surprise.
- **Text renders off-slice too.** `_view_text`, `_view_text_coords` and `_view_text_color`
  all index through `_view_indices`, which is `np.flatnonzero(_displayed)`
  (`shapes.py:1632-1697`). Measured on upstream, an in-progress shape off-origin gives
  `view_indices=[]` and `view_text=[]`; under the exemption both become non-empty, so a
  text-enabled layer will draw the in-progress shape's label over the wrong slice, in normal
  styling. Decide it, and cover it with a text-enabled test if kept.
- **The thumbnail will include the off-slice in-progress shape.** `ShapeList.to_colors`
  reads `_displayed` directly. The thumbnail already shows the in-progress shape while
  on-slice, so this is consistent, but it does mean the thumbnail stops being strictly
  "the current slice" for the duration of a draw. Low impact; decide, do not discover.

## Open questions for the maintainer

1. **Axis roll mid-draw.** Finish, cancel, or leave as-is? Finishing deletes a
   below-minimum shape. This is the one genuine product decision here.
2. Clicking off the origin slice continues the origin shape rather than starting a new one.
   That is what brisvag's sketch says; Escape-then-click gives the other behavior. Confirm
   before tests pin it.
3. Thumbnail behavior above.
4. Only one draw per layer, ever. Worth stating in the PR: it rules out the "incomplete
   contours scattered around an N-D volume" case raised in the napari#9207 thread.

## Separate issues this work surfaced

Each is reproducible on `upstream/main` without any part of this design. Filing a wrong or
redundant report costs more credibility than filing none, so each carries an explicit
destination and each is reported as **symptom plus mechanism**, without prescribing a fix we
have not demonstrated.

| # | Defect | Destination |
|---|---|---|
| 1 | `Rectangle.slice_key` truncates an unrounded bounding box while `Polygon`/`Path` round, so a rectangle drawn at a fractional layer coordinate is invisible on the slice it was drawn on | New issue, linked to napari#8901 (open, same `slice_key`-versus-view-key family, different half). Our `d9a94e8d` fixed the picking-side tolerance; this is the shape-side rounding and is independent. |
| 2 | `ShapeList.edit(..., new_type=...)` propagates `dims_order` but not `ndisplay`, raising `ValueError` on a list at `ndisplay=3` | New issue. Six-line repro, no viewer, no drawing. |
| 3 | `_move_selected_layer` dereferences `layer._selected_box` with no `None` guard, so a view change during a **SELECT-mode resize** raises `TypeError` | New issue, scoped to SELECT mode. napari#9206 removes the creation-path instance but not this one; say so. |
| 3b | napari#9206 makes rect/ellipse/line creation write full-D min/max, so a slice change mid-drag produces a slice-spanning shape that renders nowhere | **Review comment on #9206**, not an issue. |
| 4 | ~~`np.sign` zero guard, NaN on transpose mid-drag~~ | **Do not file.** napari#9206 removes the sign-based rotation and builds it from the normalized handle offset, which cannot degenerate. Verified fixed at that PR head. |
| 5 | Removing an unrelated **earlier** shape mid-draw raises `IndexError` from `_finish_drawing` | Not a new issue. napari#9277 covers the family and our open napari#9441 already clamps it; add the earlier-index case as a test **there**. |
| 6 | `Shapes._reset_editable` is not wired to `ndisplay`, so a model-only `ViewerModel` keeps `editable=True` and `mode='add_polygon'` in 3D | **Do not file.** The Qt controls reset it, so there is no user-visible consequence, and the report invites a "works as designed" answer. Raise only if a maintainer brings up the model/GUI split. |

On 3: the obvious remedy is a `None` return, and it is **not demonstrated**. Returning early
may simply strand the resize rather than end it coherently. Report what reproduces and why,
and let the maintainers pick the fix.

## Two things to settle before writing the PR

**Name the field for what it does, not what uses it.** A name like `active_index` imports
drawing vocabulary into `ShapeList`, which otherwise knows nothing about creation and should not
start. The field's actual job is "the one shape exempt from slice filtering", so something
like `unsliced_index` or `always_displayed_index` keeps the layer's concern in the layer.
A reviewer noticing `ShapeList` learning about drawing is a fair objection; the neutral
name pre-empts it and costs nothing.

**The visual cue ships as a second PR.** Settled above; recorded here so the PR body does
not quietly re-merge them.

## Next steps

1. Put the four separate issues above in the tracker, with the reproducers.
2. Ask brisvag the open questions on napari#9207.
3. One PR, slice-point only:
   - the exemption index, owned by `Shapes`, exempted in `_update_displayed` only, with a
     defensive clamp in `ShapeList`;
   - origin projection in `ShapeList.edit` when `index` is the exempt shape;
   - suspension of both invariants on a displayed-set change, detected before the
     `_data_view` batch.

   Regression tests: the off-slice round trip; a vertex placed off-slice landing on the
   origin slice; the S4 drag raise; finishing off-origin with no intervening reslice
   (asserting the committed shape is neither displayed nor pickable there); and the
   deliberate non-pickability of the off-slice in-progress shape.

   Partition coverage needs to be parametrized rather than a single "roll mid-draw" case,
   because a 4D-layer roll would pass while the case that motivates layer-space comparison
   fails. Four distinctions, each asserting **layer-local axes and resulting vertex
   coordinates**, not only visibility:

   | Case | Expected |
   |---|---|
   | `roll()` on a layer at viewer arity (displayed set changes) | suspends |
   | `transpose()` (permutation within the displayed set) | does not suspend |
   | `order` permuted within the non-displayed set only | does not suspend; anchor coordinates unchanged |
   | `roll()` seen by a 2D layer in a 4D viewer (world set changes, layer set does not) | does not suspend |

   Use a stack with no singleton axes: `Dims.roll()` skips axes that are not `rollable` or
   have `nsteps == 1`, so a singleton leading axis rolls (0,1,2,3) to (0,3,1,2) and any test
   hard-coding the post-roll order breaks.
4. Rebase or retire the navigation-lock parts of `feature/dims-nav-lock-draw-exempt` that
   this replaces. Keep the per-axis padlock (napari#9442) and the `_draw_lock_exempt`
   reasoning, which stays useful for an application that wants the veto.
