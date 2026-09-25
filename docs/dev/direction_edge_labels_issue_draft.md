# Upstream issue draft: direction_edge_labels

**Status:** Active
**Last updated:** 2026-09-25
**Scope:** proposed upstream issue for branch `feature/direction-edge-labels` (worktree `napari-feat/direction-edge-labels`)

Paste-ready; the user files it. Reviewed by Codex (gpt-6-sol).

---

Title: Feature: map per-axis direction labels to canvas edges

#9374 added `middle_left` and `middle_right` canvas positions, so an application can put orientation letters at the four edge centers (see `examples/dev/overlays.py`). What's still missing is deciding which letter goes on which edge. That depends on which two world axes are displayed (`dims.order`) and on the sign of each screen axis (`camera.orientation`). A fixed assignment goes wrong as soon as the user flips an axis or transposes the view. Every consumer currently has to re-derive this from napari's camera and dims conventions, even though it's pure viewer geometry.

I propose a small pure function in `napari.components`:

```python
direction_edge_labels(direction_labels, *, dims, camera) -> dict[str, str] | None
```

- `direction_labels` has one `(negative, positive)` pair of strings per world axis, or `None` for an unlabeled axis. napari never interprets the strings, so the same call works for anatomical (`R`/`L`), geographic (`W`/`E`) and tissue (apical/basal) orientations, including the OME-NGFF RFC-4 case that #9374 mentions.
- It returns `{'top': ..., 'bottom': ..., 'left': ..., 'right': ...}` for the current 2D view and omits unlabeled edges.
- It returns `None` for `ndisplay == 3`, or when fewer than two axes are displayed, instead of guessing.

With the middle positions, an application needs only a few lines:

```python
from napari.components import direction_edge_labels
from napari.components.overlays import TextOverlay

positions = {'top': 'top_center', 'bottom': 'bottom_center',
             'left': 'middle_left', 'right': 'middle_right'}
labels = (None, ('A', 'P'), ('R', 'L'))  # world axes (z, y, x)

edges = direction_edge_labels(labels, dims=viewer.dims, camera=viewer.scene.camera) or {}
for edge, position in positions.items():
    viewer.canvas.overlays[f'orientation_{edge}'] = TextOverlay(
        text=edges.get(edge, ''), position=position, visible=edge in edges
    )
```

Re-run it when `dims.order`, `dims.ndisplay` or `camera.orientation` changes. `labels` needs one entry per world axis, so it has to be rebuilt when `dims.ndim` changes.

The proposal covers only the function: no new model field and no new overlay. It follows up on #9250, where this came up. I have a branch with the function and tests and can open a PR if this shape works for you. Would you rather see it as a free function, as above, or as a method on the viewer?
