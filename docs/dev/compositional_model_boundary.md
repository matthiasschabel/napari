# Production and compositional-model branch boundary

**Status:** Implemented
**Last updated:** 2026-09-19
**Scope:** napari `integration`, `compositional-core`, and `explore/compositional-data-model`; pirana consumers

## Context

The compositional core was merged into integration on 2026-09-17. An audit of
pirana-lab, pirana-gui, and ohsu-projects found no consumers of its scientific
data model, coordinates, ROI/measurement, ImageBinding, or MPR APIs, including
in their tests, examples, notebooks, and explorations. The DICOM MPR demo in
napari consumes pirana data; pirana itself does not consume the new model.

## Current Decision

Integration carries the production napari fixes and extensions used by pirana.
The experimental model stays on compositional-core; the streaming, GPU atlas,
NGFF, and raw-acquisition extensions stay on explore/compositional-data-model.

The removal from integration reverses the core addition, scene line/mesh
overlays and their registration, and the experimental package's lazy-import
change. It removes the model's tests, MPR demos, extraction documents, and
model-only shapely testing dependency. It retains the general canvas guards
for partially constructed layer visuals, their QtViewer regression tests,
the pointer-move test isolation, and the slicer pending-futures snapshot.

Synchronization is one-way:

```
integration -> compositional-core -> explore/compositional-data-model
```

Merge integration into core and core into exploration when production changes
are needed there. Do not merge either experimental branch into integration.
An independently useful napari fix discovered experimentally is extracted as
a small topic change and integrated separately.

With all three worktrees clean, the routine synchronization commands are:

```sh
git -C ../napari-compositional-core merge --no-ff integration
git -C ../napari-compositional-model merge --no-ff compositional-core
```

Resolve conflicts normally and run the affected experimental tests. Keeping
production fixes upstream of the model does not make those fixes immune to
interactions with experimental behavior.

The first synchronization after this removal must retain the experimental
files explicitly: Git otherwise propagates their deletion to core and then
exploration. Record those resolutions in merge commits, not by replacing the
whole merge with the `ours` strategy, which would discard production fixes.
Once those merges are recorded, later merges carry only subsequent changes.

Removing an earlier addition does not erase its ancestry. If the model is
adopted in production later, an ordinary merge may not reintroduce unchanged
files. Restore the intentionally removed payload explicitly and validate its
new consumers rather than assuming a merge restores it.

## Alternatives Considered

- Keep the unused model in integration: increases production scope without a
  current pirana consumer.
- Revert the entire core merge: also removes independently useful napari
  correctness fixes and regression tests.
- Rebase or rebuild integration: needlessly rewrites the production assembly
  and threatens unrelated fork changes and published dependency pins.

## Deferred Work

The published fork pin at e33f37a6a still contains the model. It remains valid
and must remain reachable. Moving the reproducible fork environments requires
publishing and retaining the replacement integration commit, then advancing
the dependency URLs, uv source revisions, locks, and metadata checkout pins
in pirana-gui, pirana-lab, and ohsu-projects together. Local branch separation
does not itself change those pinned environments.

## Next Steps

Keep experimental development downstream of integration. Reconsider production
adoption only alongside a concrete pirana consumer and its acceptance tests.

### Verification on 2026-09-19

- Production napari: layer-slicer tests, 21 passed; QtViewer, canvas, and
  layer-linking tests, 99 passed and 23 skipped.
- Pirana against the separated production source: capability, ImageScroller,
  navigation-lock, ROI bridge, and colormap-manager adapter tests, 414 passed
  and 1 skipped.
- Synchronized core: all data-model tests plus scene geometry overlay tests,
  590 passed.
- Synchronized exploration: all data-model tests, 1370 passed.
- Ruff check and format check passed for the three modified Python files in
  the production removal.
- Core's 55 model/test files and exploration's 93 model/test files are
  unchanged from their pre-synchronization versions. Production has none.
  The retained canvas, slicer, and QtViewer tests are also unchanged.
- Both experimental branches contain the production removal in their
  ancestry; `git merge-tree` confirms merging it again preserves their trees.

Napari suites used the scipy-dev Python 3.14 environment; pirana used its
Python 3.12 environment. Each run selected the intended napari source with
PYTHONPATH. GUI runs used the real macOS display serially, and the main test
processes used temporary cache/settings directories. These are focused
compatibility checks, not the full napari or three-repository release bar.
