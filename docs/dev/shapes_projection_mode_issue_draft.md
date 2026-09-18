# Draft issue: Shapes ignores thick slicing

**Status:** Active
**Last updated:** 2026-08-25
**Scope:** Draft for a new napari issue. Independent of napari#9207.
Refined against a codex pass; dispositions at the foot of this file.

---

## Shapes cannot participate in thick slicing

`Shapes` accepts `projection_mode` and forwards it to `Layer.__init__`, but declares no
`_projectionclass`, so the only accepted value is `none`. `ShapeList` never reads the
thick-slice margins.

### What we are trying to do

- Draw an ROI once, then refine it while stepping a **parametric** axis, comparing it against
  several echo times, b-values, or channels.
- Keep the same ROI fixed on the **spatial** axis, so stepping z never propagates a contour
  onto a plane it was not drawn on.
- Both in one viewer, without the image itself being projected.
- None of it requiring napari to know which axis is which.

### What blocks it

- `Shapes._projectionclass` is unset, so `projection_mode` cannot leave `none`.
- `ShapeList.slice_key` is built from `_data_slice.point` alone, so margins are dropped and a
  wide thick slice changes nothing for a Shapes layer.
- `ShapeList._update_displayed` matches a shape's slice key against that point within half a
  slice. There is no window to widen.
- `ShapeList._visible_shapes`, which drives picking, tests only whether the point falls inside
  a shape's own slice range. It is likewise margin-blind, so picking needs the same treatment
  as rendering rather than coming along for free.

### Why the per-axis control is already there

`Dims.margin_left` and `margin_right` are per-axis tuples, and `projection_mode` is per layer,
so a viewer can show one echo of an image while another layer spans all of them. Image,
Points, Vectors and Surface each declare a projection class; Shapes, Labels and Tracks do not.

On `main` (`908b3ed8`), in an `(echo, z, y, x)` viewer with one point per `(echo, z)` and the
margin widened on `echo` only:

```
image  projection_mode='none' -> one echo, margins ignored
image  projection_mode='mean' -> aggregated across the echo margin
Points projection_mode='all'  -> every echo at the current z
Shapes projection_mode='all'  -> ValueError: 'all' is not a valid BaseProjectionMode
```

The spatial-versus-parametric distinction never reaches napari. It is carried by the margins,
which the user already sets per axis. napari should not try to infer projection policy from
axis labels or units.

### Proposal

1. Give `Shapes` a projection class with `none` and `all`.
2. Under `all`, have both `_update_displayed` and `_visible_shapes` test a shape against the
   thick-slice window rather than the point, so a projected shape is selectable wherever it is
   drawn.

**Scope it to slice-flat shapes first.** That is the motivating case and it is unambiguous: a
shape whose vertices share their non-displayed coordinates either falls in the window or does
not. Shapes may legitimately span a range, and for those "compare against the window" could
mean containment, overlap, or clipping, each rendering different geometry. Points do not have
this problem because a point has one coordinate per axis; Surface faces it and resolves it by
filtering vertices and keeping only faces whose vertices all survive. A minimal first rule
would be to require a shape's whole slice range to lie inside the window, leaving partial
overlap for a follow-up.

### Relation to #7

#7 includes a proposal for a checkbox in the thick-slicing popup that sets an axis margin to
its maximum, though the thread notes the design is not settled. That would be the natural
control for this: tick it on the parametric axis, leave the spatial axis alone, and a Shapes
layer in `all` mode behaves correctly on both. The two changes are independent and useful
separately, but they compose.

### Not this issue

napari#9207 concerns in-progress shapes during a slice change, which is a drawing problem.
This is about how committed shapes are sliced.

---

## Notes for us, stripped before posting

**Codex dispositions, all accepted.**

- **"Picking already works this way" was wrong and is removed.** I conflated two things.
  `_visible_shapes` tests whether the *point* lies inside a shape's own slice span, so a
  shape *spanning* echoes is pickable at an intermediate echo. A slice-flat ROI at echo 0 is
  **not** pickable at echo 1 no matter how wide the margin. #7459 introduced point-based
  filtering to stop out-of-slice shapes being selected; it did not establish thick-slice
  picking. The motivating case is the flat one, so picking has to be part of the proposal, not
  a precedent for it. This also kills the tidy "rendering catching up to picking" framing.
- **Range-spanning projection is genuinely undefined** and is now flagged as a decision rather
  than glossed. Surface is the citable precedent for how a mesh layer resolves it.
- **Attributions were overstated.** #9207 argued the policy belongs to the *application*, not
  that it belongs in margins; that claim is dropped. #7 has support for the checkbox but jni
  explicitly said more design thought is needed, so "converged on" became "includes a proposal
  for".
- **"Every other layer already does this" was false.** Labels and Tracks are also `none`-only.
  Verified: `_projectionclass` is declared by Image, Points, Vectors and Surface, and by
  nothing else. The claim now names the four.
- **The bare "3 points" was not reproducible from the text**, so the probe block is qualitative.
- **The µm/nm argument was too absolute** and is gone. Labels and units may carry semantic
  clues; the defensible claim is only that napari should not *infer* policy from them.

**Other notes**

- Margins are viewer-wide, so two Shapes layers wanting different windows on the same axis
  cannot both be served. Not a blocker for us; do not raise it unprompted.
- Setting margins is discoverable only by right-clicking a slider, which is why linking #7
  is worth doing.
- Attach the actual probe script if the issue gets traction; the block above is deliberately
  qualitative.
