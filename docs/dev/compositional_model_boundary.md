# Production and compositional-model branch boundary

**Status:** Active (the 2026-09-19 separation is implemented; its reversal is decided, not done)
**Last updated:** 2026-10-11
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

Resolve conflicts normally and run the affected experimental tests. The
data-model suite needs Python 3.13 or newer (`copy.replace`), and `-p no:napari`
cannot be used inside this tree because `src/napari/conftest.py` has an autouse
fixture that depends on the napari pytest plugin. Keeping
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

## Decision of 2026-10-11

The model returns to integration as an additive feature, `napari.experimental.data_model`,
reversing the separation above. The September objection, no pirana consumer, still holds;
the reason to reverse anyway is that integration is the branch that tracks upstream, so a
feature carried there costs nothing in pirana and can move to pirana later if upstream
declines it, whereas a feature parked in pirana first has no upstream path. The price is
ongoing compatibility testing of the adapters' private napari touches on every upstream sync,
in exchange for retiring the one-way branch synchronization. Conditions (plan-reviewed by
Codex, gpt-6-astra at high effort, 2026-10-11; one blocking and three non-blocking findings
folded in):

- Scope is the compositional-core model: identity, fields, domains, sources, the coordinate
  layer and the napari adapters. The progressive renderer (atlas, bricks, ladder, working set)
  on the exploration branch is archived, not merged.
- The coordinate layer is merged frozen. It is a less complete xarrayrf and will be replaced
  by xarrayrf once xarrayrf's field representation settles; no xarrayrf dependency is taken now.
  The spike's `_layer_coordinates` is the Image and `ImageBinding` projection seam only. A full
  swap also touches `DataObject` validation (concrete `CoordinateEmbedding`, intrinsic-frame
  identity), MPR's direct embedding reads, and the layer round-trip adapters, so it is a
  migration, not a one-function change.
- The existing boundary test, `_tests/test_core_imports.py`, is the enforcement: model modules
  never import `napari.layers`, `napari.components`, `napari._qt` or `napari._vispy`, and
  shapely stays an optional, function-local import for ROI geometry. Model modules do use
  napari's existing non-viewer dependencies, pint (unit validation, basis conversion) and
  lazy_loader; "NumPy only" was never true of this model and is not a condition.
- The adapters' private napari touches are inventoried in that test's docstring and checked on
  each upstream sync: MPR reads slicer state, imports the private Shapes polygon model and
  writes Shapes' `_is_creating`.
- `DataSource` is canonical; pirana's `NDImageSource` adapts to it (pirana-lab,
  `docs/dev/data_model_layering.md`).
- No Python floor change. Production code does not need `copy.replace`; `DataObject` has its
  own `replace` and rejects `dataclasses.replace` by design. The merge guards the two unguarded
  `copy.replace` assertions in `test_mri.py` for Python below 3.13, and the model suite runs on
  the fork's 3.11 floor before the merge is called compatible.

After the merge, the one-way synchronization and both experimental worktrees are retired, and
the branches archived under `refs/archive/<date>/`.

## Next Steps

Perform the merge above as one reviewed change on integration, then move the pirana pins
(Deferred Work).

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
