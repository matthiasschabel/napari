# Integration checkout tools

Setup for working on the `integration` branch of the fork, plus `nrepro` for
comparing a repro script between stock napari (`upstream/main`) and integration.

## Setting up a new machine

Prerequisites: `git` and [`uv`](https://docs.astral.sh/uv/) (`brew install uv` on macOS).
uv downloads the Python it needs, so no system Python is required.

```shell
git clone -b integration https://github.com/matthiasschabel/napari.git ~/GitHub/napari
~/GitHub/napari/tools/integration/bootstrap.sh
```

`bootstrap.sh` does four things. Each is skipped if already done, so it is safe to re-run:

1. Adds the `upstream` remote (`napari/napari`, push disabled) and fetches it.
2. Creates `.venv` (Python 3.12) and installs napari editable with the `pyqt6` and
   `testing` extras. This step always runs, so re-running after a pull picks up
   dependency changes.
3. Creates the stock worktree, detached at `upstream/main`, beside the checkout
   (`~/GitHub/napari-stock` for the clone above).
4. Links `nrepro` into `~/.local/bin`. It warns if that directory is not on `PATH`;
   add it in `~/.zshrc` with `export PATH="$HOME/.local/bin:$PATH"`.

It ends by printing the installed napari version. To check the install:

```shell
.venv/bin/python -m pytest src/napari/components/_tests/test_dims.py -q
.venv/bin/napari          # opens the viewer
```

Environment overrides:

| Variable | Default | Purpose |
|---|---|---|
| `NAPARI_PYTHON` | `3.12` | Python version for `.venv` |
| `NAPARI_STOCK_DIR` | `<checkout>-stock` | Stock worktree location (`nrepro` reads it too) |
| `NAPARI_BIN_DIR` | `~/.local/bin` | Where to link `nrepro`; set empty to skip |

### Optional extras

- **Compositional-model remote.** Only needed for the data-model experiments:
  `git remote add private https://github.com/matthiasschabel/napari-compositional.git`
- **Agent conventions.** `AGENTS.md` and `CLAUDE.md` are gitignored and come from the
  `agents-conventions` repo. Clone it beside this one, then:

  ```shell
  ~/GitHub/agents-conventions/sync-conventions install
  ~/GitHub/agents-conventions/sync-conventions link ~/GitHub/napari --project napari
  ```

  Re-run `link` after adding a worktree so it gets the files too.

## Running repros with `nrepro`

```shell
nrepro stock repro.py [args...]   # against upstream/main
nrepro int   repro.py [args...]   # against this checkout
nrepro both  repro.py [args...]   # stock, then int; exits with int's status
nrepro update                     # move the stock worktree to the latest upstream/main
```

Both trees run on the integration `.venv`; `PYTHONPATH` selects which source is
imported. Each run gets a throwaway `NAPARI_CONFIG`, so a repro cannot change your
real napari settings.

## Keeping up to date

```shell
git pull
tools/integration/bootstrap.sh    # picks up dependency changes
nrepro update                     # refreshes stock
```
