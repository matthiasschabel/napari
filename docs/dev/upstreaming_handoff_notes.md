# Upstreaming handoff: 2026-09-24/25 session

**Status:** Active
**Last updated:** 2026-09-25
**Scope:** napari fork worktrees, open upstream napari/vispy PRs, pirana-gui orientation labels

## Resume here

1. Recheck the PRs in "Waiting on others" (`gh pr view <n> -R napari/napari` / `-R vispy/vispy`) for review activity.
2. The user is reviewing draft PRs #9561–#9565 and will mark them ready themselves.
3. **direction-labels** (below): issue #9571 and draft PR #9572 are up; integration is pushed.

Read the repo's `AGENTS.md` first. Every post, PR, or comment upstream needs the user's explicit go-ahead for that specific item.

## Objective

Rationalize the fork's napari worktrees and move finished fixes upstream as small, reviewable PRs, keeping `integration` (pirana's production assembly) in sync. Bookkeeping lives in [branch_inventory.md](branch_inventory.md) ("Open upstream PRs", "Upstream PRs that differ from integration", "Related vispy work").

## Waiting on the user

- **#9561–#9565 drafts:** the user reviews them and marks them ready. #9563 changes the ADDED event payload, and its body says so.
- **direction-labels:** mark draft #9572 ready; decide on merging pirana's `feature/orientation-text-overlays` and deleting the superseded `feature/direction-labels` branch (local and fork).

## Waiting on others

| Item | Waiting for | Then |
|---|---|---|
| #9442, model-only axis lock | Maintainers on two questions in the body: should a refused write raise (brisvag's idea)? Keep guarding direct `point` assignment? | Adjust #9442, then restack #9568 |
| #9568, padlock GUI ("Depends on #9442") | #9442 | Rebase onto the merged #9442. Needs a padlock screenshot from the user. |
| vispy#2796 (NaN test compares against `gl_DepthRange.far`), with vispy#2795 (`Image.bad_color` raised since vispy#2663) | vispy review | When #2796 is inside napari's minimum vispy, close napari #9561 (a stopgap). |
| vispy#2798 (issue: CPU-scaled NaN color depends on the driver) | vispy triage | We offered a PR replacing NaN with 0 in `_scale_data_on_cpu` if they agree. |
| #9372 (issue: infinity colors) | A maintainer reply | Then decide `feature/colormap-inf-colors`; new public API, so hold until then. |
| #9562, then #9563 (stacked) | Review of #9562 | Rebase #9563 after #9562 merges. |

When #9563, #9565, or #9442/#9568 merge, reconcile integration. The biggest difference is that #9563 reports negative ADDED indices while integration reports positive ones, so check pirana's `data` listeners first (inventory, "Upstream PRs that differ from integration").

## direction-labels (done 2026-09-25, local only)

The user approved the three-part plan. Each part was reviewed by Codex (gpt-6-sol) with no blocking findings.

- **Upstream:** issue #9571 (filed by the user); draft PR #9572 from `feature/direction-edge-labels` (worktree `napari-feat/direction-edge-labels`), reviewed by gpt-6-sol and gpt-6-astra. It adds only `napari.components.direction_edge_labels` (`versionadded:: 0.10.0`) and its tests. The user marks it ready.
- **Fork:** the integration commit "Drop the direction-labels overlay in favor of the upstream helper" removes `DirectionLabelsOverlay` and `viewer.direction_labels`, and syncs the helper to the upstream version. Components and vispy tests: 833 passed. Unpushed. The stale `origin/feature/direction-labels` and the local `feature/direction-labels` (worktree `napari-feat/direction-labels`) are superseded; deleting them needs the user's say-so.
- **Pirana:** branch `feature/orientation-text-overlays` in pirana-gui. The letters are four stock `TextOverlay`s at `top_center`, `bottom_center`, `middle_left` and `middle_right`, which removes the `_scene_canvas` reach. The full `--gui` suite: 4123 passed, and the 2 `test_coordinates` failures also fail on `main`. Tested only against the pinned fork (`e33f37a6`). A `PYTHONPATH` override cannot test integration in that venv, because napari refuses the mixed install. The letters are no longer bold.

## Parked

- **Fast/exact preference for `gpu-exceptional-colors`.** The user is thinking it over. The question is whether a preference should switch between GPU-native and correct rendering of out-of-range values. Summary given:
  - 2D in-shader classification costs a few comparisons per pixel. The NaN-only version measured no frame-time change (Apple M5, 32 stacked 1024² layers, ±5% noise). The full branch has not been measured.
  - Infinity colors need classification on every GPU, because vispy clamps before the colormap. So a preference could only mean "infinity colors on or off", not a workaround for GPUs that handle NaN correctly.
  - The costly designs are the CPU sanitize-plus-class-texture alternative (a per-upload O(N) scan, one byte per pixel, an extra texture read), 3D volumes (cost times ray samples), and exact linear-filter borders (a one-texel NaN halo when magnified).
  - Suggested position: 2D classification always on; any preference belongs with volumes and filtering. See `docs/dev/exceptional_rendering/README.md` for the probe findings.
- **Promise to napari maintainers (2026-08-07):** the user said they would tighten the descriptions of their existing open issues and PRs. It is still outstanding. Drafts only; the user posts every edit.

## Decisions

- **#9442 split** (maintainer request): the private `_point_refused` event moved to #9568 with its only consumer. Raising on a refused write is deliberately left as an open question in #9442. The pre-split tip is saved at `refs/archive/2026-09-24/feature/dims-axis-lock-presplit` (local only).
- **vispy NaN fix:** chose `gl_DepthRange.far`, a built-in uniform clamped to [0, 1] that needs no API, over napari's sentinel approach (#9561) and over a `$flt_max` uniform in the colormap GLSL. The uniform would break every consumer of `glsl_map`.

## State snapshot (observed 2026-09-25)

- **napari worktrees:** all clean, no stashes, no background jobs. Live PR worktrees are under `~/GitHub/napari-feat/`:
  - `dims-axis-lock` (#9442, `336c64d68`) and `dims-axis-lock-gui` (#9568, `fb2a892d4`),
  - `nan-color-fast-math` (#9561, `001eaa69b`),
  - `shapes-finish-drawing-emit-order` (#9562, `7cf6e6d7d`),
  - `shapes-added-event-indices` (#9563, `7660d72a6`),
  - `shapes-data-setter-shape-type` (#9564, `97c11fcd2`),
  - `tiled-image-state` (#9565, `b169cf7d5`).

  All are pushed to `origin`, the user's fork.
- **Local-only branches:** `feature/colormap-inf-colors`, `dev/gl-exceptional-probe`, and `feature/gpu-exceptional-colors`. Every commit on them is already in `integration`, so nothing is at risk.
- **vispy clone (`~/GitHub/vispy`):** remote `fork` is `matthiasschabel/vispy`, on `main`, clean. The PR branches `fix/image-bad-color-property` and `fix/nan-test-survives-fast-math` are pushed. Running from source needs `_sdf_cpu*.so` and `version.py` copied from the scipy-dev site-packages; both are already there and git-ignored.
- **vispy test results:** in `vispy/visuals/tests/test_image.py`, every failure except the NaN tests disappears with `QT_SCALE_FACTOR=0.5`; the rest are Retina artifacts from rendering at 2x. With both vispy PRs applied, all GPU-path NaN tests pass. The CPU-path NaN tests still fail; that is vispy#2798. Across the visuals and scene suites, 15 failures are identical on stock and patched vispy. They were not individually diagnosed.

## Gotchas (with working commands)

- **Codex CLI:** add `< /dev/null`, as in `codex exec -m gpt-6-astra -s read-only -C <repo> -o <out.md> "<prompt>" < /dev/null`. Without it, a background run hangs on "Reading additional input from stdin...".
- **zsh:** `git show $B:path` applies a `:s` modifier to `$B`. Quote it: `git show "${B}:path"`.
- **Import path:** a script run from inside `~/GitHub/vispy` imports the clone, because the working directory is on the path. To test the installed vispy, run from a neutral directory.
- **Proving a test fails without its fix:** when the fix is already committed, `git stash` does not remove it. In a clean worktree (`git status` empty), run `git show <pre-fix-sha>:<file> > <file>`, run the test, then restore with `git checkout -- <file>`. Never do this over uncommitted edits.
- **napari tests:** `PYTHONPATH=<worktree>/src /opt/miniconda3/envs/scipy-dev/bin/python -m pytest <paths> -q -p no:cacheprovider --maxfail=1000`. Lint with `~/GitHub/napari/.venv/bin/pre-commit run --files <files>`.
- **Committing notes:** napari's `.gitignore` ignores `docs/`, so new files under `docs/dev/` need `git add -f`.

## Done this session

For outcomes, see [branch_inventory.md](branch_inventory.md) and `refs/archive/2026-09-24/` (dead branches, local only). In brief:
- **Worktrees:** six dead worktrees removed and five dead branches archived.
- **integration:** upstream `main` merged.
- **napari PRs:** #9561–#9565 opened, and #9568 opened after the #9442 split.
- **vispy:** PRs #2795 and #2796 opened, and issue #2798 filed.
- **Compositional branches:** pushed to the private remote.
- **Tooling:** `/handoff` skill added in `~/GitHub/skills`.
