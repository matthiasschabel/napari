# Upstreaming handoff: 2026-09-24/25 session

**Status:** Active
**Last updated:** 2026-09-25
**Scope:** napari fork worktrees, open upstream napari/vispy PRs, pirana-gui orientation labels

## Context

The session covered five things. It rationalized the napari worktrees, removed dead ones and archived dead branches. It opened seven napari PRs and two vispy PRs, and filed a vispy issue. It split #9442 into model and GUI parts. It reviewed the direction-labels branch against upstream. Branch and PR bookkeeping lives in [branch_inventory.md](branch_inventory.md); this note holds what is unfinished, what is waiting on others, and the facts a new session would otherwise re-derive.

## Current Decision

### Waiting on the user

- **Review #9561–#9565 and mark them ready.** All are drafts; the user said they will review them on 2026-09-25. #9563 changes the ADDED event payload and says so in its body.
- **direction-labels.** A recommendation was given (below) and the user has not chosen yet.

### Waiting on others

| Item | Waiting for | Then |
|---|---|---|
| #9442 (model-only axis lock) | Maintainers: should a refused write raise (brisvag's idea)? Keep guarding direct `point` assignment? | Adjust #9442, restack #9568 |
| #9568 (padlock GUI, "Depends on #9442") | #9442 | Rebase onto merged #9442; add a padlock screenshot (needs the user) |
| vispy#2796 (NaN test via `gl_DepthRange.far`) + vispy#2795 (`Image.bad_color` fix) | vispy review | When #2796 is in napari's minimum vispy, close napari #9561 (it is a stopgap) |
| vispy#2798 (CPU-scaled NaN is driver-dependent) | vispy triage | Offered a PR replacing NaN with 0 in `_scale_data_on_cpu` if they agree |
| #9372 (infinity colors issue) | Maintainer reply | Then decide on `feature/colormap-inf-colors` (new public API; hold) |
| #9562 → #9563 stack | Review of #9562 | Rebase #9563 when #9562 merges |

### Upstream PRs whose merge needs integration reconciliation

When #9563, #9565, or #9442/#9568 merge, see "Upstream PRs that differ from integration" in branch_inventory.md. The most important one: #9563 uses negative ADDED indices, while integration uses positive ones. Check pirana's `data` listeners before switching.

### direction-labels: recommendation given, not acted on

- The local `feature/direction-labels` (worktree `napari-feat/direction-labels`) is canonical. The fork copy is a stale pre-rebase version with nothing unique. Integration ships identical code.
- Pirana uses **only** the helper `napari.components.direction_edge_labels` (`pirana-gui/packages/pirana-viewer/src/pirana/viewer/image_scroller.py:1926`). It falls back to its own mapping on stock napari. It draws the letters with private vispy `Text` nodes parented to `_scene_canvas`. Nothing uses the fork's `DirectionLabelsOverlay` or `viewer.direction_labels`.
- Upstream #9374 (jni, in napari 0.9.0) added `middle_left/center/right` canvas positions for orientation letters. It is part of image-coop's OME-NGFF RFC-4 work, and its example (`examples/dev/overlays.py`) adds one custom `CanvasOverlay` per letter. On #9250, brisvag said arbitrary positions and per-grid-cell overlays are not planned.
- Proposed path:
  1. Pirana: replace the private Text nodes with four canvas overlays at `top_center`/`bottom_center`/`middle_left`/`middle_right`. Check pirana's floor (0.9.0rc1) against #9374, which is confirmed only in 0.9.0 final.
  2. Upstream: propose only the edge-mapping helper, via a #9250 comment or a new issue tagging jni. Convert its `trans._()` strings to f-strings first.
  3. Fork: drop the unused overlay and `viewer.direction_labels` from integration, and force-update or delete the stale fork branch (needs the user's say-so).
- Offered next: draft the upstream comment and the pirana overlay change.

### Parked (user's call, no action requested)

- **Fast/exact preference for `gpu-exceptional-colors`.** The perf summary is in the conversation: in-shader 2D classification is cheap; the CPU sidecar ("Option B"), 3D volumes, and the linear-filter border are where a preference would earn its keep. No frame-time numbers exist for that branch yet.
- **Outstanding commitment to tidy existing open napari issues/PRs** (see memory `napari-cleanup-commitment`).

## Alternatives Considered

- **#9442 split.** Raising on a refused write was deliberately not done in the split; it is now an open question in the #9442 body. The private `_point_refused` event moved to #9568 with its only consumer.
- **vispy NaN fix.** Considered napari's sentinel approach (#9561), a new `$flt_max` uniform in the colormap GLSL (would break every consumer of `glsl_map`), and `gl_DepthRange.far`. Chose `gl_DepthRange.far`: a built-in uniform clamped to [0, 1] that needs no API.

## Deferred Work

- **vispy RGB NaN path:** also changed in #2796, but no visible bug on the M5, because `clamp(NaN)` happens to return 0.
- **vispy local test failures:** most are Retina artifacts (render at 2x). Run vispy tests with `QT_SCALE_FACTOR=0.5`.

## Next Steps

1. User reviews #9561–#9565 and marks them ready.
2. Decide the direction-labels path, then draft the #9250 comment or new issue and the pirana overlay change.
3. React to reviews on #9442/#9568 and the vispy PRs.

### Facts and gotchas that cost time this session

- **Codex CLI:** run `codex exec ... "<prompt>" < /dev/null`. Without it, a background run hangs on "Reading additional input from stdin...". Model `gpt-6-astra` was used for reviews.
- **zsh:** `git show $B:path` applies a `:s` modifier to `$B`. Quote it: `"${B}:path"`.
- **Python import path:** running a script from inside `~/GitHub/vispy` imports the clone, not site-packages, because cwd is on the path. Run from a neutral dir to test released vispy.
- **vispy clone:** `~/GitHub/vispy` has remote `fork` = `matthiasschabel/vispy`. Running it from source needs `_sdf_cpu*.so` and `version.py` copied from the scipy-dev site-packages (already done).
- **Checking that a test fails without its fix:** when the fix is already committed, `git stash` does not remove it. Use `git show HEAD~1:<file> > <file>`, then restore.
- **Test env:** `/opt/miniconda3/envs/scipy-dev/bin/python -m pytest ... -p no:cacheprovider --maxfail=1000` with `PYTHONPATH=<worktree>/src`. Lint with `~/GitHub/napari/.venv/bin/pre-commit run --files ...`.
- **Archived refs:** dead branches are under `refs/archive/2026-09-24/` (local only), including `feature/dims-axis-lock-presplit`.
