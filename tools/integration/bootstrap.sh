#!/usr/bin/env bash
# Set up an integration checkout on a new machine:
#   - add the `upstream` remote (push disabled) and fetch it
#   - create .venv with uv and install napari editable, with PyQt6 and test deps
#   - create the stock worktree (detached at upstream/main) that `nrepro stock` runs against
#   - link `nrepro` into ~/.local/bin
#
# Safe to re-run: every step checks its result first and skips if it's already done.
# Only prerequisites: git and uv.
#
# Environment overrides:
#   NAPARI_PYTHON     Python version for .venv (default 3.12)
#   NAPARI_STOCK_DIR  stock worktree location (default: <checkout>-stock beside the checkout)
#   NAPARI_BIN_DIR    where to link nrepro (default ~/.local/bin; set empty to skip)
set -euo pipefail

UPSTREAM_URL=https://github.com/napari/napari.git
root=$(git -C "$(dirname "$(realpath "$0")")" rev-parse --show-toplevel)
stock=${NAPARI_STOCK_DIR:-$root-stock}
bin_dir=${NAPARI_BIN_DIR-$HOME/.local/bin}
py=${NAPARI_PYTHON:-3.12}

log() { printf '[bootstrap] %s\n' "$*" >&2; }

for tool in git uv; do
  command -v "$tool" >/dev/null || { log "missing prerequisite: $tool"; exit 1; }
done

if git -C "$root" remote get-url upstream >/dev/null 2>&1; then
  log "upstream remote present"
else
  log "adding upstream remote"
  git -C "$root" remote add upstream "$UPSTREAM_URL"
fi
# Checked separately so an upstream remote that already existed also loses its push URL.
if [[ $(git -C "$root" remote get-url --push upstream) == DISABLED ]]; then
  log "upstream push already disabled"
else
  log "disabling push to upstream"
  git -C "$root" remote set-url --push upstream DISABLED
fi
log "fetching upstream"
git -C "$root" fetch --quiet upstream

if [[ -x $root/.venv/bin/python ]]; then
  log ".venv present"
else
  log "creating .venv (Python $py)"
  uv venv --quiet --python "$py" --prompt napari "$root/.venv"
fi
# Also run when .venv already exists, so dependency changes from a pull get picked up.
log "installing napari (editable) with pyqt6 and testing extras"
VIRTUAL_ENV=$root/.venv uv pip install --quiet -e "$root[pyqt6,testing]"

# A new worktree has no _version.py, because setuptools-scm only writes it at install time.
if [[ -d $stock ]]; then
  log "stock worktree present at $stock (refresh with: nrepro update)"
else
  log "creating stock worktree at $stock"
  git -C "$root" worktree add --quiet --detach "$stock" upstream/main
fi
cp "$root/src/napari/_version.py" "$stock/src/napari/_version.py"

if [[ -n $bin_dir ]]; then
  mkdir -p "$bin_dir"
  ln -sfn "$root/tools/integration/nrepro" "$bin_dir/nrepro"
  log "linked $bin_dir/nrepro"
  case ":$PATH:" in *":$bin_dir:"*) ;; *) log "note: $bin_dir is not on PATH" ;; esac
fi

log "done: $("$root/.venv/bin/python" -c 'import napari; print("napari", napari.__version__)')"
