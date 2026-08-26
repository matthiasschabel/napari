# Changelog

Maintainer-facing record of fixes carried in this fork. Design rationale for the
exceptional-value work lives in `docs/dev/exceptional_rendering/README.md` and, more fully,
in `cmap/docs/dev/mcslab/napari_exceptional_rendering_plan.md`.

## [2026-08-26] — Shapes vertex editing uses distinct add and remove cursors

- **Problem**: Shapes vertex insertion and removal both used the generic cross cursor, so
  the pointer did not indicate which editing operation was active.
- **Resolution**: added semantic `add` and `remove` cursor styles backed by high-contrast
  SVG pixmaps, rendered through QtSvg with Qt 5/6-compatible device-pixel-ratio handling,
  and selected them for the corresponding Shapes modes.
- **Files affected**: `src/napari/components/_viewer_constants.py`,
  `src/napari/components/cursor.py`, `src/napari/layers/shapes/shapes.py`,
  `src/napari/_vispy/canvas.py`, `src/napari/_vispy/utils/cursor.py`,
  `src/napari/_vispy/_tests/test_utils.py`, `src/napari/resources/_icons.py`, and
  `src/napari/resources/cursors/`
- **Reviewed by**: Codex GPT-5 (model ID unavailable)

## [2026-08-23] — A navigation lock stops moving the active slider, and stays visible

- **Problem**: two regressions this fork introduced on top of its own per-axis lock, both
  absent from the upstream branch behind the lock PR. Locking an axis moved `last_used` off
  it, so lock and unlock no longer round-tripped, `dims.last_used = <locked axis>` was
  silently reverted by the validator, and the owner lock tier disagreed with the per-axis
  tier because private attributes do not re-run the validator. Separately, the disabled
  handle rule was written to cover the `last_used` attribute selector as well, so every
  locked handle was the same grey and a navigation lock cost the user sight of which axis
  the arrow keys would resume on — visible during ROI drawing, and the common case once
  locking stopped moving focus.
- **Resolution**: `_check_dims` goes back to upstream's visibility-only rule. The premise
  behind the extra condition does not hold: an arrow key on a locked active axis emits
  `axis_lock_rejected` and flashes the padlock, so the user is told, not stranded.
  `_focus_up`/`_focus_down` still skip locked axes, which leaves a pointer press as the only
  route back to one, so the row now claims the axis when a press lands on its frozen slider
  (standing in for the `sliderPressed` a disabled scrollbar never emits). The two QSS rules
  split apart, and a locked *active* handle greys out like the others but keeps a 1px
  `current` rim.
- **Considered and rejected**: marking the locked active handle with a muted `current` fill,
  which is what the upstream branch does. `darken()` moves a colour towards the theme
  background, so in the light theme `darken(current, 25)` is `rgb(177, 202, 255)` against an
  enabled `rgb(160, 184, 255)`; the handle reads as live. The rim is full-strength in either
  theme and leaves the fill free to mean "disabled".
- **Upstream**: the focus regression never reached the lock PR's branch, so nothing to send.
  The rim is an improvement over what that branch styles today and is staged there locally,
  to go out with the response to its pending review rather than as separate churn.
- **Files affected**: `src/napari/components/dims.py`,
  `src/napari/_qt/widgets/qt_dims_slider.py`,
  `src/napari/_qt/qt_resources/styles/02_custom.qss`,
  `docs/dev/per_axis_navigation_lock_design.md`, and their tests

## [2026-08-22] — Exceptional values are classified before the contrast limits are applied

- **Problem**: three defects with one cause. The float image pipeline clamped data into the
  contrast limits before the colormap saw it, which makes an infinity indistinguishable
  from a saturated finite value, so `neg_inf`/`pos_inf` could not have their own colors at
  all. It left `>= 1` and `<= 0` as the only way to notice an out-of-range value afterwards,
  which also caught values sitting exactly on a limit, diverging from matplotlib's
  `set_under`/`set_over` while `nan_color` was documented as matplotlib's `bad_color`. And
  NaN was detected with `!(d <= 0.0 || 0.0 <= d)`, a self-comparison that a fast-math
  compiler may fold away; Apple's GL-on-Metal does, so NaN rendered as the bottom of the
  colormap rather than `nan_color` on Apple Silicon. Separately, tiled images kept the stock
  shader, and the layer thumbnail clipped before mapping, so both disagreed with the canvas.
- **Resolution**: `apply_clim` classifies NaN, both infinities, and finite out-of-range
  values before it clamps, passing the class to the colormap as an out-of-band sentinel that
  a prologue decodes ahead of every check vispy injects. `apply_gamma` passes sentinels
  through, since `pow()` of a negative base is NaN. The NaN test compares against two
  uniform bounds the compiler cannot relate, because every single-bound form is provable
  under a no-NaN assumption and folds; a test asserts the shape of that expression. Decoding
  is opt-in and enabled only for the image and tiled-image nodes, because vispy's mesh
  visual feeds the colormap an unclamped normalized value that would otherwise be misread as
  a class. Tile children now use napari's visual and carry their pass-through state across a
  rebuild. The thumbnail keeps NaN and the infinities out of its clamp, since clipping
  collapses an infinity onto a limit and gamma turns -inf into NaN. Fallback resolution
  delegates to `Colormap.map`, so the shader cannot disagree with the thumbnails about what
  a class looks like.
- **Considered and reverted**: making `low_color`/`high_color` strict at the endpoints, to
  match matplotlib's `set_under`/`set_over`. napari's automatic contrast limits are the
  data's own minimum and maximum, so the extreme pixels of a freshly loaded image sit
  exactly on the limits; under a strict rule the stock `HiLo` colormap would flag nothing on
  the images it exists for, and its two-entry table has nowhere to bake the colors instead.
  The inclusive rule is load-bearing rather than an artifact. No existing behavior changed.
- **Not covered**: the volume path composes its own shaders and still has the NaN defect;
  masked data has no channel to the GPU. Both are recorded as deferred.
- **Files affected**: `src/napari/utils/colormaps/colormap.py`,
  `src/napari/utils/colormaps/colormap_utils.py`, `src/napari/_vispy/visuals/image.py`,
  `src/napari/_vispy/layers/image.py`, `src/napari/_vispy/layers/tiled_image.py`,
  `src/napari/layers/image/image.py`, and their tests
- **Reviewed by**: Codex gpt-5.6-sol (reasoning effort xhigh), four passes; Claude Code,
  Claude Fable 5 (claude-fable-5)
