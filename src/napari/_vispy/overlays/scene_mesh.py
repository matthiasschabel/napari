from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from vispy.scene.visuals import Mesh

from napari._vispy.overlays.base import ViewerOverlayMixin, VispySceneOverlay
from napari.utils.events import disconnect_events

if TYPE_CHECKING:
    from napari.components.overlays import SceneMeshOverlay


class VispySceneMeshOverlay(ViewerOverlayMixin, VispySceneOverlay):
    overlay: SceneMeshOverlay
    node: Mesh

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(node=Mesh(), **kwargs)
        self.overlay.events.triangles.connect(self._on_data_change)
        self.overlay.events.color.connect(self._on_data_change)
        self.viewer.dims.events.order.connect(self._on_data_change)
        self.viewer.dims.events.range.connect(self._on_data_change)
        self.viewer.dims.events.ndisplay.connect(self._on_data_change)
        super().reset()
        self._update_data(raise_on_mismatch=True)

    def _should_be_visible(self) -> bool:
        return self.overlay.visible and bool(len(self.overlay.triangles))

    def _on_data_change(self) -> None:
        self._update_data(raise_on_mismatch=False)

    def _update_data(self, *, raise_on_mismatch: bool) -> None:
        triangles = self.overlay.triangles
        ndisplay = self.viewer.dims.ndisplay
        if len(triangles):
            if triangles.shape[2] != self.viewer.dims.ndim:
                self.node.visible = False
                if raise_on_mismatch:
                    raise ValueError(
                        'triangle dimensionality must match viewer.dims.ndim'
                    )
                return
            displayed = self.viewer.dims.displayed[::-1]
            vertices = triangles[..., displayed].reshape(-1, len(displayed))
            faces = np.arange(len(vertices), dtype=np.uint32).reshape(-1, 3)
            color = self.overlay.color
        else:
            vertices = np.zeros((3, ndisplay), dtype=np.float32)
            faces = np.array([[0, 1, 2]], dtype=np.uint32)
            color = np.zeros(4, dtype=np.float32)
        self.node.set_data(vertices=vertices, faces=faces, color=color)
        self._on_visible_change()

    def reset(self) -> None:
        super().reset()
        self._on_data_change()

    def close(self) -> None:
        disconnect_events(self.viewer.dims.events, self)
        self.overlay.events.triangles.disconnect(self._on_data_change)
        self.overlay.events.color.disconnect(self._on_data_change)
        super().close()
