# Proposal: keep an in-progress shape coherent across slice changes

**Status:** Active
**Last updated:** 2026-08-25
**Scope:** Draft proposal for napari#9207. Design rationale and evidence in
`shapes_drawing_session_design.md`.

## What goes wrong now

Change slice while drawing a polygon and the shape vanishes. It is still there, still
selected, still `is_creating` — but every vertex placed from that moment carries the new
slice's out-of-plane coordinate, so the shape ends up spanning two slices. A shape whose
slice key spans is drawn on *neither*, so it stays invisible even after you navigate back.
For the polygon family this happens on the first mouse *move* after the slice change, not on
the next click.

There is a second, sharper symptom. Step a slider with the arrow keys while holding a
rectangle drag and it raises `TypeError: 'NoneType' object is not subscriptable`, because
the interaction box is rebuilt from the shapes in view and the shape being drawn is no
longer one of them.

## The proposal

Three mechanisms, and the third exists only to keep the first two honest.

**1. Anchor new vertices to the slice the draw started on.** While a draw is open, vertex
coordinates written for that shape have their not-displayed components forced to the origin
plane. The origin needs no bookkeeping: it is the first vertex's out-of-plane coordinate,
and the first vertex is written once at draw start and never rewritten during creation.
Geometry can then never span slices, whatever the user does with the sliders.

**2. Exempt the shape being drawn from slice filtering.** One index on `ShapeList` marks a
single shape as always displayed. Everything that follows from visibility — the outline, the
vertex handles, the interaction box, the selection highlight — comes back for free, because
they all derive from the same displayed mask. This is also what stops the drag raising.

**3. Suspend both if the displayed axes change.** Rolling axes or toggling 2D/3D changes
*which* axes are in-plane, and there is then no single origin value to anchor to. Rather
than guess, both mechanisms switch off for the rest of the draw and behaviour reverts to
what napari does today. This is enforced in code, not just promised in prose: without it,
an unconditional exemption would quietly change axis-roll behaviour too.

## What it touches

| Where | Change |
|---|---|
| `ShapeList.edit` | Project onto the origin plane when editing the exempt shape |
| `ShapeList._update_displayed` | Honour the exempt index; defensive clamp so the list's own mutations cannot raise |
| `Shapes` | Own the exempt index's lifecycle: set at draw start, clear on finish or discard, recompute the displayed mask after either |
| `Shapes._set_view_slice` | Detect a displayed-axis-set change before the data view is reconfigured, and suspend |

Four functions and one private field. No new public API, no new settings, no changes to the
`Shape` models or the VisPy adapter.

Two details worth stating because they are easy to get wrong. The displayed-set comparison
must be in **layer** axis space: a world roll changes the world's displayed axes while a 2D
layer's own axes are untouched, and comparing world axes would suspend a layer that is
entirely unaffected. And the exempt index's lifecycle belongs to `Shapes`, not `ShapeList` —
there are two indices for the same shape, and only the layer can see both.

## Effect on existing drawing

**For anyone who does not change slice mid-draw: nothing changes.** The exemption only ever
applies to a shape under construction, and the anchor only rewrites coordinates that would
otherwise have moved off the origin plane. On a single slice both are no-ops.

**For anyone who does:** the shape stays visible and keeps its handles instead of vanishing;
vertices placed from another slice land on the origin slice; navigating back resumes the
draw exactly where it was; and the drag no longer raises.

Unchanged: `layer.data` and `nshapes` still go live at the first vertex, the `ADDING` →
`ADDED` sequence is untouched, selection behaves as it does after #9059, z-order and
committed-shape hit testing are not affected, and finishing produces the same shape on the
same slice as before.

Two consequences worth a decision rather than a discovery, both following from the exemption
rather than being separately designed:

- A text-enabled layer will render the in-progress shape's label while off-slice, in normal
  styling.
- The thumbnail will include the in-progress shape while off-slice. It already includes it
  while on-slice.

Deliberately **not** included: hit testing. `inside()` and `shapes_in_box()` keep their own
slice filter, so the off-slice in-progress shape is not pickable. Nothing in creation needs
it to be, and the two filters differ on purpose for shapes that legitimately span slices.

## Not in this change

- **The off-slice colour cue.** napari has one configurable highlight colour applied
  uniformly, and no second semantic colour. "A different colour" needs a source, a
  light/dark behaviour and an accessibility expectation before it can be written, so it is
  better as a follow-up than as a colour argument attached to a bug fix.
- **Partition policy.** See below.
- **Four unrelated defects** found while verifying this, each reproducible on `main` without
  any of the above: `Rectangle` keying to the wrong slice at fractional coordinates,
  `ShapeList.edit` dropping `ndisplay` on a type change, and two ways drag-based creation
  breaks when the view changes mid-drag. Separate reports.

## The open question: rolling axes mid-draw

Rolling axes mid-draw is the one case this proposal deliberately leaves alone, because every
remedy is a product decision rather than a correctness one.

| | Option | Cost |
|---|---|---|
| 1 | Leave it (what this change preserves) | Shape vanishes; the draw silently continues onto the new plane |
| 2 | Finish the draw | Deletes a path under 3 vertices or a polygon under 4. Safe for rectangle, ellipse and line, which always hold four |
| 3 | Cancel the draw | Destructive by definition |
| 4 | Refuse the roll while a draw is open | Non-destructive, but no clean place to enforce it |
| 5 | Freeze the draw and resume when the original axes return | Non-destructive; the draw picks up where it left off |

**Option 5 reads best**, and it is the same principle as "go back to the original slice and
continue", applied to axes instead of slices: hide the shape exactly as today, decline to
extend it, and resume when the displayed axes are back. Rolling away and back is verified to
restore the draw with the vertices untouched. It costs one extra piece of state, the
displayed axes at draw start, which genuinely cannot be recovered from anything already
stored.

**Option 4 is worth a maintainer opinion** rather than a recommendation from outside.
`Dims.rollable` already exists and `roll()` respects it — marking the displayed axes
non-rollable turns an unsafe roll into a harmless permutation of the remaining axes, which is
neat. But it is a preference consulted by `roll()`, not enforcement: `transpose()`, a direct
`dims.order` assignment, `ndisplay`, and the axis-list drag all ignore it. Real enforcement
would mean `Dims` asking whether a layer is mid-draw, and whether that inversion is
acceptable is a call for people who own the codebase.

Worth being explicit, since it is adjacent to an approach already turned down: this is not a
proposal to block slice navigation. Moving between slices mid-draw is useful, and the change
above is what makes it safe. Rolling axes is a different thing, because the plane the
geometry lives in is what changes.
