# Zulip draft: Shapes and slicing

**Status:** Active
**Last updated:** 2026-08-25
**Scope:** Draft message for napari Zulip (#general or a Shapes-adjacent stream). Not posted.

Intent: ask one question, supply just enough evidence to make it credible, and let the
maintainers decide whether this is one piece of work or several. Not a roadmap.

---

Working on #9207 I ended up reading a lot of the Shapes slicing code, and I keep running
into small inconsistencies that all seem to be the same thing wearing different hats. Wanted
to sanity-check whether that's a real pattern or just me staring at it too long.

The ones I can reproduce on main:

- **Rendering and picking disagree about what "on this slice" means.** `_update_displayed`
  requires a shape's slice key to match the current point within half a slice. `_visible_shapes`
  asks whether the point falls inside the shape's own min/max range. A shape spanning two
  slices is therefore selectable but never drawn.

- **Slice keys round differently per shape type.** `Polygon` and `Path` use `np.rint`, `Line`
  uses `np.round`, `Rectangle` and `Ellipse` cast to int. Because `Rectangle` casts an
  *unrounded* bounding box, a rectangle drawn at a fractional layer coordinate (easy to hit
  with a non-integer translate) is invisible on the slice you just drew it on, while a
  polygon at the same coordinate is fine.

- **Shapes ignores thick slicing entirely.** `ShapeList` only ever sees `_data_slice.point`,
  and `Shapes` never declares a `_projectionclass`, so `projection_mode` can't leave `none`.
  Image, Points, Vectors and Surface all support it. (Labels and Tracks don't either, so
  Shapes isn't unique, but it's the one where per-object projection would be most useful.)

- **`ShapeList.edit` drops `ndisplay`** when it converts a shape's type, so the replacement
  shape can end up 2D inside a 3D list.

- **The "no drawing in 3D" guard lives in the Qt layer controls**, not the model, so a
  headless `ViewerModel` will happily sit in `add_polygon` mode at `ndisplay=3`.

None of these block each other, and I'm happy to just file them as separate small issues.
But they do all look like the same underlying thing: Shapes' notion of which slice it's on is
ad hoc and doesn't line up with how the rest of the layers do it.

So: is that worth a coherent pass at some point, or would you rather these stay independent
fixes? Genuinely fine either way, just don't want to file five tickets if the answer is
"yes, that whole area needs rethinking anyway."

---

## Notes for us

- Post one message, then stop. Do not follow up with a document unless asked.
- If the answer is "file them separately", do exactly that, one at a time, smallest first.
- If the answer is "that area needs rework", *then* the `design_related.md` issue template is
  the sanctioned lane (labeled `design`, auto-assigned).
- Deliberately omitted: #9207 itself, the drawing-state scatter, and anything about our own
  open PRs. Mentioning the PR queue turns a question into a status report.
