# Per-axis navigation pin: contract, scope and staging

**Status:** Active — blocked on maintainer response to napari#9278
**Last updated:** 2026-07-26
**Scope:** napari `Dims` per-axis navigation pin — `src/napari/components/dims.py`,
`src/napari/_qt/widgets/qt_dims.py`, `qt_dims_slider.py`,
`src/napari/_qt/qt_resources/styles/02_custom.qss`

> Working note for the upstream contribution. Not shipped in any PR diff (`.gitignore`
> ignores `docs/`; this file is force-tracked on `feature/dims-navigation-lock` alongside
> [per_axis_navigation_lock_design.md](per_axis_navigation_lock_design.md) and
> [navigation_lock_v2_design.md](navigation_lock_v2_design.md)).

## Where this stands (2026-07-26)

- **napari#9278** — proposing issue filed, awaiting maintainer response. No code is to be
  written against this note until there is a signal that the feature is wanted and that the
  enforcement boundary below is acceptable.
- **napari#9275** — PR-1 (`Shapes.is_creating` + `drawing_started`/`drawing_finished`,
  closes #9208) open and awaiting review. It is the other half of the seam and is independent
  of this work.
- The reference implementation on this branch (`feature/dims-navigation-lock`) is **pre-#8935**
  and still uses `trans._()`; it must be rebased onto current `upstream/main` and its own
  strings converted to f-strings before any of it goes up. See the fork's memory note on that
  rebase.

## Context

An application drawing ROIs on N-D data needs to freeze *some* slice axes and not others.
Motivating case: multi-acquisition DICOM, trailing axes `(z, y, x)` spatial, leading axes
parametric (echo time, flip angle, b-value). Drawing a spatial ROI on one z-slice must freeze
`z`; the user still wants to step parametric axes mid-draw to compare contrast while the
contour stays live.

napari cannot decide which axes are "compatible" — only the application knows. So the ask is a
**seam**, not a policy: napari exposes the signal (#9208 / PR-1), the pin (this note), and a
geometry backstop (PR-4); the application composes them.

The transient *owner lock* on `feature/dims-navigation-lock` is **out of upstream scope**.
`navigation_lock_v2_design.md` (2 Codex passes) rejected a field-level `__setattr__`
chokepoint; a further review on 2026-07-25 falsified the claim that wrapper enforcement is
"fully honest for the UI". This note records what survived.

## Current Decision

### Staging — PR-2 and PR-3 are one PR

Model and Qt ship together as **one feature PR**: *per-axis navigation pin + padlock*.

Reason is risk asymmetry, not seam mechanics. Split, the bad outcome is real: the model PR
merges, the UI PR is rejected on UX taste, and napari carries a public `lock_axis` API with no
consumer. Merged, rejection costs nothing. Nothing upstream has *requested* axis pinning, so a
headless-API-only PR also has no good answer to "why does napari need this?" — the padlock is
the answer.

Sequence: **file a proposing issue first** (matching what has worked for #9203 / #9207 / #9208),
then submit against it.

PR-5 (amber flash on a rejected poke) stays separate — genuine polish.

### Scope

**In:**

- `axis_locked: tuple[bool, ...]`; `lock_axis`, `unlock_axis`, `lock_all_axes`,
  `unlock_all_axes`; `is_axis_locked(axis)`. Axes addressable by index or `axis_labels` name
  (raises on unknown/ambiguous).

  Named `is_axis_locked`, not `is_axis_movable`: the latter was the *precedence-ladder* query
  from the owner-lock design (`force` > owner-exempt > user pin), abstracting over tiers so views
  asked one question. With the owner tier dropped it is exactly `not axis_locked[axis]` — an
  abstraction over nothing, in a name that breaks the lock/unlock paradigm. It still earns being a
  method rather than field indexing because it accepts labels. Consequence: "movable" also
  quietly covered *not a usable slider* (`nsteps == 1`, or a displayed axis); the Qt code must now
  say that explicitly rather than let one predicate stand for two concepts.
- `axis_lock_interactive: bool = True` — gates the padlock click path only, never the methods,
  so an application can drive pins itself and leave the padlocks as indicators.
- `axis_locked` and `axis_lock_rejected` events.
- Qt: padlock button per slider row; per-child enablement (the padlock stays clickable on a
  frozen axis; `axis_label` stays enabled — renaming is not navigation); disabled-handle
  styling; `last_used` and `_focus_up`/`_focus_down` skip axes that are locked or are not usable
  sliders (the two conditions stated separately — see the naming note above).

**Cut — `order` / `ndisplay` guarding.** A pin has no opinion about axis order or 2D/3D.
Rationale: there is no wrapper to hook (`ndisplay` has no setter method; `_view.py:151` and
`qt_dims_sorter.py:23` assign the fields raw), so model-level enforcement is unachievable
without the rejected chokepoint. Guarding it would have meant either a blanket block — one
stale pin freezing the 2D/3D toggle viewer-wide — or a targeted rule that still depends on UI
cooperation. Documented answer to "what if I reorder?": reordering can make a pinned axis
displayed, which makes the pin moot; the pin is not a guarantee about *which* axes are sliced.

### The contract

> **A pin blocks navigation of that axis for as long as the pinned coordinate remains valid.**

- **Guarded:** `set_point`, `set_current_step`, `_increment_dims_*`, and every UI path funnelling
  through them (slider drag, wheel, arrow keys, slice-number editor, playback).
- **Not guarded (documented):** raw field assignment (`dims.point = ...`,
  `dims.current_step = ...`, `dims.order = ...`), and data-driven `range`/`ndim` clipping.

### Blocked-write semantics — observable no-op

Blocked navigation is a **no-op that emits `axis_lock_rejected`**, never an exception.

Raising was considered and rejected on two independent grounds. First, `set_point` already
*coerces* out-of-range coordinates rather than raising (`dims.py:217-221`); throwing on a locked
axis would be inconsistent with the method's own convention. Second, `qt_dims_slider.py:141`
(slice editor), `:165` (slider drag) and `qt_dims.py:354` (playback) call `set_current_step`
directly, so raising would surface tracebacks out of Qt callbacks.

The original objection to a silent partial write — that silence is what hid #9203 for years —
is met by the event: the rejection is observable and testable, and PR-5's flash consumes it.

`force=` is **retained** (internal): `ViewerModel._on_ndisplay_changed` calls
`dims.set_point(new_display_dim, center[0])` (`viewer_model.py:532`) on exactly the axis moving
from displayed to sliced during a 3D→2D transition.

### Pin lifetime

napari releases a pin **only when its coordinate ceases to exist** (the data shrank past it),
emitting an event. Nothing else clears pins automatically.

Invalidating on layer-stack change was considered and rejected: `inserted`/`removed` fire for
*any* membership change, so an unrelated annotation or plugin-output layer would silently clear
a user's padlock, and an application adding a layer mid-draw would kill the pin protecting the
draw. More fundamentally, **napari cannot detect a dataset swap** — it has no dataset identity;
`layers/_source.py` records per-layer provenance precisely because that inference is the
consumer's. So:

> Pins are viewer-axis state bound to the current coordinate space. An application that
> redefines that space (loading a new study) should call `unlock_all_axes()`.

## Why the viewed slice cannot be absolutely guaranteed

1. **The coordinate space is data-driven and wins.** `ViewerModel._on_layers_change` assigns
   `dims.ndim`/`dims.range` directly (`viewer_model.py:801-810`) and `_check_dims` clips `point`
   into the new range. Verified on `main`: `Dims(ndim=3, range=((0,10,1),)*3, point=(9,0,0))`
   then `range = ((0,2,1),)*3` yields `point == (2.0, 0.0, 0.0)`. If the data supplying slice 9
   is gone, slice 9 does not exist; no lock can hold a coordinate the model no longer has.
2. **Raw assignment carries no intent.** `dims.point = x`, the `current_step` setter
   (`dims.py:260`) and `set_point` (`dims.py:379`) all converge on one field write through
   `EventedModel.__setattr__` (`evented_model.py:254`). A guard there cannot distinguish
   validator normalisation, lifecycle rebuild, a vetted method, and an external caller
   navigating; recovering intent needs trusted ambient state whose footgun is that any future
   method writing `point` silently self-blocks if it forgets the flag. napari already carries one
   such flag (`_validating`, `dims.py:116`/`:525`).
3. **So the guarantee is scoped to navigation, not to the coordinate.** "Nothing can change the
   viewed slice" is unachievable. "No navigation action moves a pinned axis while its coordinate
   is valid" is, and is what ships.
4. **Residual risk has a backstop.** A pin can end legitimately mid-operation. For drawing that
   would mean later vertices landing elsewhere — which is why PR-4's `_creation_anchor` pins
   in-progress vertices to their origin slice at the *geometry* layer, independent of any lock.
   Defence in depth: the pin prevents the common case, the anchor makes a scattered shape
   impossible even when the pin legitimately ends.

## Alternatives Considered

- **Field-level chokepoint (v2).** Rejected. Does not close the largest gap — layer-driven
  `range` clipping must remain permitted under any design (limit 1) — while costing a hot-path
  `__setattr__` override, a second trusted-state flag, and contract decisions on `reset`,
  `_go_to_center_step` and `EventedModel.update()`.
- **Split model PR / UI PR.** Rejected — stranded-API risk; see staging.
- **Raise on blocked writes.** Rejected — see semantics.
- **Clear pins on layer insert/remove.** Rejected — see lifetime.
- **Blanket or targeted `order`/`ndisplay` blocking.** Both cut — see scope.

## Deferred Work

- Field-level enforcement, only if a real raw-assignment bypass is demonstrated. Kept viable by
  wording the non-guarantee as *"not enforced at the field level; do not rely on it"* rather than
  *"the intended escape hatch"*. The branch test that pins the bypass as contract
  (`test_dims.py:732-741`) must be reworded to match, or it contradicts the softer docstring.
- `order`/`ndisplay` interaction with pins, if users ask.
- PR-5 amber flash.

## Next Steps

1. ~~File the proposing issue~~ — done, napari#9278 (2026-07-26).
2. **Wait for maintainer response on #9278.** Two things to listen for: whether the feature is
   wanted at all, and whether the enforcement boundary above is the one they would choose. The
   `## Scope` section of the issue is where pushback is expected.
3. On a positive signal, build the **merged model+Qt PR** off a fresh branch from current
   `upstream/main`: port from this branch, convert its `trans._()` strings to f-strings, drop
   the `order`/`ndisplay` guarding, rename `is_axis_movable` → `is_axis_locked`, and trim the
   430-line `test_dims.py` addition to the contract's assertions.
4. Codex `/collaborative-refinement` review before opening.

## Review history

Four Codex passes (gpt-5.6-sol, high) shaped this note. Findings worth not re-deriving:

- The production Shapes hook called `lock_navigation(layer, lock_order=True)` with `exempt=()`
  — every axis frozen. The application-configurable seam the owner-lock design assumed **never
  existed**; `exempt=` appears only in tests. That is what moved draw protection out of napari
  and into application composition.
- "Wrapper enforcement is fully honest for the UI" is **false**: deleting a layer is a UI action
  that rewrites `dims.range` and clips a pinned `point`. Verified empirically.
- A field-level chokepoint would **not** have fixed that, since layer-driven `range` clipping
  must remain permitted under any design. Enforcement depth was the wrong axis to argue about;
  contract precision was the right one.
- Playback would wedge on a silent no-op: `_set_frame` clears `_play_ready` before calling
  `set_current_step` and it only resets after a canvas draw (`qt_dims.py:351-354`,
  `canvas.py:1318`). Hence the explicit "locking a playing axis stops playback" rule.
- `axis_labels` are **not** required to be unique (`Dims(ndim=3, axis_labels=('z','z','x'))` is
  accepted), so label addressing needs a defined disambiguation rule; it was demoted to a
  discussion point in #9278 rather than presented as settled.
