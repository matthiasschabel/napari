# Changelog

Maintainer-facing record of fixes carried in this fork. Design rationale for the
exceptional-value work lives in `docs/dev/exceptional_rendering/README.md` and, more fully,
in `cmap/docs/dev/mcslab/napari_exceptional_rendering_plan.md`.

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
  rebuild. `Colormap.map` uses strict comparisons and the thumbnail no longer clips.
  Fallback resolution delegates to `Colormap.map`, so the shader cannot disagree with the
  thumbnails about what a class looks like.
- **Known behavior change**: the stock `HiLo` colormap marks only values outside the limits
  rather than values at them. Raised upstream as a draft issue rather than a quiet fix.
- **Not covered**: the volume path composes its own shaders and still has the NaN defect;
  masked data has no channel to the GPU. Both are recorded as deferred.
- **Files affected**: `src/napari/utils/colormaps/colormap.py`,
  `src/napari/utils/colormaps/colormap_utils.py`, `src/napari/_vispy/visuals/image.py`,
  `src/napari/_vispy/layers/image.py`, `src/napari/_vispy/layers/tiled_image.py`,
  `src/napari/layers/image/image.py`, and their tests
- **Reviewed by**: Codex gpt-5.6-sol (reasoning effort xhigh), four passes; Claude Code,
  Claude Fable 5 (claude-fable-5)
