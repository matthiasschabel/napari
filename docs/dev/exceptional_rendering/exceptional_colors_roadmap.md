# NaN and ±inf colors in napari: roadmap

**Status:** Active
**Last updated:** 2026-11-03
**Scope:** napari `Colormap` model and `map()`, colormap conversion to and from vispy, image,
tiled-image and Surface layers, thumbnails, the float64 → float32 data boundary. The napari half
of the cross-repo plan; the vispy half is `~/GitHub/vispy/docs/dev/exceptional_rendering_plan.md`
(local only).

## Context

Goal: a napari user can give NaN, -inf and +inf their own colors beside `low_color`/`high_color`,
and the canvas, thumbnails, and Surface layers render them correctly on fast-math drivers.

- cmap #151 and #155 are merged and released (v0.8.0). `cmap.Colormap.to_napari()` forwards NaN
  (preferring `nan` over `bad`), under → `low_color`, over → `high_color`, nothing for
  infinities, because napari has no fields for them.
- vispy owns rendering. Its PRs #2796 (fast-math-safe NaN test), #2795 (`Image.bad_color`) and
  #2804 (CPU-path NaN) are open and ready. Infinity colors in vispy (U8a-c) are designed and
  endorsed in vispy#2803 but not started.
- napari renders images on vispy's GPU-scaled path (`texture_format='auto'`) whenever the GPU has
  float textures, and falls back to the CPU-scaled path only without them
  (`_vispy/layers/image.py:29-38`). So #2796 is what fixes NaN for most napari users; #2804
  covers the fallback.
- napari pins `vispy>=0.17.0,<0.18`.
- napari#9561 (draft, now conflicting) is a napari-side stopgap for #8056. A vispy maintainer
  asked to continue in vispy instead.
- The fork's `integration` branch holds the prototypes: model fields `neg_inf_color`/
  `pos_inf_color` (Stage 1), GPU classification with template overrides (Stage 2, superseded by
  vispy U8a-c), the thumbnail fix (`51881ee4`), and the GL probe in this directory.

Contract (same as vispy): NaN takes `nan_color` on both paths; infinity colors are ramp-relative,
which for napari means "+inf is always `pos_inf_color`" since contrast limits are always
increasing; exact under `nearest` only; unset colors change nothing, and an unset infinity color
falls back to `low_color`/`high_color`, as cmap falls back to under/over.

## Current Decision

Napari ends up with no shader code for exceptional values: it supplies colors and clean data,
and vispy renders them.

### Steps

| Step | Change | Depends on | Notes |
|---|---|---|---|
| U6 | Raise napari's minimum vispy to the first release with #2796 and #2804; close #9561 | a vispy release with both | Fixes #8056. Independent of the infinity work. The pin bump is a napari maintainer decision. |
| U10 | Clip finite float64 values above FLT_MAX before the float32 cast in `fix_data_dtype`, keeping real infinities | - | Small, independent. Today invisible; becomes a defect once infinities have their own color. |
| U10s | Same clip for Surface vertex values, which bypass `fix_data_dtype` and are cast to float32 in vispy `mesh.py:330` | - | Separate PR, or defer Surface overflow explicitly. |
| U7 | `neg_inf_color`/`pos_inf_color` on `Colormap`, applied in `map()` after the low/high checks; `same_colors()` compares them; `_napari_cmap_to_vispy` pops them until vispy accepts them; `cmap.to_napari()` forwards them by feature detection | napari#9372 accepted | Prototype exists (Stage 1). Without the pop, every conversion raises `TypeError` on vispy without the keywords. |
| U7t | Thumbnail normalizes only finite values, so NaN and infinities reach `map()` (`layers/image/image.py:589-594` clips them, and `(-inf) ** gamma` is NaN) | U7 | Prototype `51881ee4`. `map()` alone does not fix it. |
| U9 | Pass the infinity colors to vispy in `_napari_cmap_to_vispy`, and back in `convert_vispy_colormap` (which copies special colors field by field) | U7; a vispy release with U8a and U8b | Second pin bump. |
| U9s | Surface with gamma ≠ 1 builds a bare `VispyColormap(colors)` from a sampled ramp (`_vispy/layers/surface.py:143-152`), dropping all special colors | vispy U8c for infinities | Already loses NaN/low/high today, so the NaN/low/high half can go now. |
| C1 | cmap `to_vispy()` forwards only ramp colors and controls, dropping bad/under/over | - | cmap PR, with feature detection for older vispy. Not on napari's critical path. |

Order: U6 and U10 can go as soon as their dependencies allow and don't wait on the rest. U7 and
U7t need a napari maintainer to accept the API first. U9 and U9s wait for vispy's second release.

### Maintainer decisions

- **napari#9372** (no maintainer reply since 2026-08): the issue still asks for strict `<`/`>` at
  the contrast limits as well as infinity colors. Strict endpoints were tried and reverted
  deliberately (auto contrast limits put the extremes exactly on the limits, and HiLo depends on
  it), and the issue names the fields `neginf_color`/`posinf_color` while cmap and this plan use
  `neg_inf_color`/`pos_inf_color`. Narrow the issue to the infinity colors with cmap's names
  before asking for a decision.
- **vispy pin bumps** for U6 and U9.

### Tiled and multiscale images

Upstream tiled images preserve and replay colormap state on retile (napari#9565, merged 2026-10-19),
so they need no separate exceptional-value work once the colormap carries the colors.

## Alternatives Considered

- napari-side shader fixes (#9561, the Stage 2 template overrides and sentinels): superseded by
  vispy's include and pass-through, which every vispy consumer gets.
- Strict low/high endpoints: rejected; see above.

## Deferred Work

- Masked values (Stage 3): napari has no mask in its data model; cmap routes masked to `bad`.
- Volumes: exceptional voxels are unspecified in every mode.
- RGB/RGBA NaN.
- Vendor-driver rows for the GL probe (see `README.md` here).

## Next Steps

1. Once vispy releases #2796 and #2804: U6.
2. U10 and U10s now, as small independent PRs.
3. Edit napari#9372 down to the infinity colors, then U7 and U7t when it is accepted.
4. U9 and U9s after vispy U8a-c are released.

The fork's prototypes are the starting point for U7 and U7t; the Stage 2 GPU code is not
proposed upstream.
