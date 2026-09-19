# Vectors Color-Mapping Controls

**Status:** Deferred
**Last updated:** 2026-08-25
**Scope:** Vectors color API and the legacy and dynamic Qt layer-control paths

## Context

PR [#9335](https://github.com/napari/napari/pull/9335) adds a colormap chooser
and contrast-limits slider for feature-mapped Vectors colors. It is stacked on
the event fix in [#9396](https://github.com/napari/napari/pull/9396).

On 2026-08-01, @brisvag suggested replacing the Vectors-specific control work
with two steps:

1. Rename `edge_colormap` and the other Vectors-only `edge_*` properties to
   plain names because Vectors has only one color and width.
2. Reuse the controls introduced by
   [#9318](https://github.com/napari/napari/pull/9318).

That suggestion was made while #9318 discovered controls by attribute name.
The merged implementation instead registers controls explicitly by layer type.
It also keeps separate legacy and dynamic widget trees during the experimental
rollout. The container uses legacy controls for the active layer by default and
dynamic controls for multiple selection or when
`experimental.dynamic_layer_controls` is enabled.

The rebased #9335 branch only updates the legacy
`widgets/_vectors/qt_edge_color.py`. Its controls therefore disappear when the
dynamic path is selected. Force-pushing that branch would leave the feature
half-wired.

The generic dynamic colormap control can operate on Vectors after a plain-name
API migration. The generic contrast-limits control cannot be reused wholesale:
it assumes the full intensity-layer contract, including
`contrast_limits_range`, `dtype`, auto-contrast, histogram, gamma, and reset
methods. Adding `IntensityVisualizationMixin` to Vectors would expose APIs that
do not affect vector rendering.

## Current Decision

Do not force-push or extend the current #9335 implementation. Leave the work
deferred pending feedback from @brisvag or a concrete need that justifies
supporting both Qt control paths now.

If the work resumes, preserve `ColorManager` as the single owner of color state
and expose that state through a canonical plain Vectors API:

```text
Vectors ColorManager
        |
        v
plain Vectors API
color / color_mode / color_cycle
colormap / contrast_limits / width
        |
        +-- legacy single-layer controls
        +-- dynamic controls
              +-- shared colormap control
              +-- narrow mapping-range slider
```

The likely candidate rename family is:

| Existing name | Candidate canonical name |
|---|---|
| `edge_color` | `color` |
| `edge_color_cycle` | `color_cycle` |
| `edge_color_mode` | `color_mode` |
| `edge_colormap` | `colormap` |
| `edge_contrast_limits` | `contrast_limits` |
| `edge_width` | `width` |

The exact family and compatibility period require maintainer confirmation.
Because these are public constructor arguments, properties, serialized state
keys, and events, the migration should be its own PR. Prefer canonical new
names with deprecated aliases over immediate removal unless maintainers request
a direct break.

After the API migration, rework #9335 as a separate GUI PR:

- Explicitly register Vectors for the shared dynamic colormap control. The
  final #9318 registry is type-based, so matching attribute names alone do not
  create controls.
- Reuse or extract a narrow contrast-range slider that depends only on
  `contrast_limits`, `contrast_limits_range`, and their events. Do not make
  Vectors an intensity-visualization layer.
- Derive `contrast_limits_range` from the currently mapped numeric feature,
  widened to contain limits set through the API.
- Keep the Vectors color-feature coordinator responsible for showing the
  colormap and limits only in colormap mode.
- Update both the legacy and dynamic widget trees while #9318's transitional
  split remains. Test the two runtime paths separately.
- Let #9396 remain an independent bug fix and land before rebasing the GUI
  work.

## Alternatives Considered

| Approach | Assessment |
|---|---|
| Force-push the current branch | Rejected. It updates only the default legacy path and silently loses the controls in the dynamic path. |
| Port the current Vectors-specific widget code to both trees | Functional but duplicates the architecture @brisvag asked to replace and leaves the public naming mismatch intact. |
| Add `IntensityVisualizationMixin` to Vectors | Rejected. Histogram, gamma, and auto-contrast would become public Vectors APIs without corresponding rendering semantics. |
| Wait until dynamic controls replace the legacy path | Architecturally smallest, but there is no active follow-up or known schedule for that transition. |
| Rename the API, reuse narrow controls, and support both paths | Preferred if the feature becomes necessary before the legacy controls are retired. |

## Deferred Work

- Confirm whether @brisvag intended all six Vectors `edge_*` names to change.
- Confirm whether old names should be deprecated and the release in which they
  may be removed.
- Decide whether the project wants a narrow contrast-limits protocol or a
  smaller composable control without formal runtime protocol discovery.
- Decide whether #9335 should support both control trees or wait for dynamic
  controls to become the default.
- Points color mapping remains separate because Points must retain distinct
  face and border color APIs.

## Next Steps

When feedback or urgency revives the work:

1. Reply on #9335 with the post-merge #9318 constraints and ask for the exact
   rename and deprecation scope.
2. Prepare the public API migration as a standalone PR, including constructor,
   property, state-key, and event compatibility tests.
3. Rework #9335 on top of that migration. Add one regression test for the
   default legacy path and one for the dynamic path, and verify both tests fail
   without the GUI change.
4. Run the complete touched test files, pre-commit on changed files, and an
   independent implementation review before handoff.
