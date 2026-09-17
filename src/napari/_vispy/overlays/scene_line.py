from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from vispy.scene.visuals import Line

from napari._vispy.overlays.base import ViewerOverlayMixin, VispySceneOverlay
from napari.utils.events import disconnect_events

if TYPE_CHECKING:
    from napari.components.overlays import SceneLineOverlay


class VispySceneLineOverlay(ViewerOverlayMixin, VispySceneOverlay):
    overlay: SceneLineOverlay
    node: Line

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            node=Line(connect='segments', method='gl', antialias=True),
            **kwargs,
        )
        self.overlay.events.segments.connect(self._on_data_change)
        self.overlay.events.color.connect(self._on_data_change)
        self.overlay.events.width.connect(self._on_data_change)
        self.viewer.dims.events.order.connect(self._on_data_change)
        self.viewer.dims.events.range.connect(self._on_data_change)
        self.viewer.dims.events.ndisplay.connect(self._on_data_change)
        super().reset()
        self._update_data(raise_on_mismatch=True)

    def _should_be_visible(self) -> bool:
        return self.overlay.visible and bool(len(self.overlay.segments))

    def _on_data_change(self) -> None:
        self._update_data(raise_on_mismatch=False)

    def _update_data(self, *, raise_on_mismatch: bool) -> None:
        segments = self.overlay.segments
        if len(segments):
            if segments.shape[2] != self.viewer.dims.ndim:
                self.node.visible = False
                if raise_on_mismatch:
                    raise ValueError(
                        'segment dimensionality must match viewer.dims.ndim'
                    )
                return
            displayed = self.viewer.dims.displayed[::-1]
            positions = segments[..., displayed].reshape(-1, len(displayed))
        else:
            positions = np.zeros(
                (2, self.viewer.dims.ndisplay), dtype=np.float32
            )
        self.node.set_data(
            pos=positions,
            connect='segments',
            color=self.overlay.color,
            width=self.overlay.width,
        )
        self._on_visible_change()

    def reset(self) -> None:
        super().reset()
        self._on_data_change()

    def close(self) -> None:
        disconnect_events(self.viewer.dims.events, self)
        self.overlay.events.segments.disconnect(self._on_data_change)
        self.overlay.events.color.disconnect(self._on_data_change)
        self.overlay.events.width.disconnect(self._on_data_change)
        super().close()
