"""Qt shell for the model-level multiplanar reconstruction controller."""

from __future__ import annotations

import logging
from contextlib import suppress
from functools import partial
from math import isfinite
from numbers import Real
from typing import TYPE_CHECKING, Any

from qtpy.QtCore import Qt, QTimer, Signal
from qtpy.QtWidgets import QGroupBox, QSplitter, QVBoxLayout, QWidget

from napari._qt.qt_viewer import QtViewer
from napari.experimental._data_model._mpr import (
    MPRController,
    MPRStreamingSession,
)
from napari.layers import Shapes

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from napari.components import ViewerModel


logger = logging.getLogger('napari.experimental._data_model._mpr_qt')

# Coarse-tier fill on the motivating remote store measured 14-20 s once fine
# reads were suppressed; 30 s covers that with margin while bounding how long
# a dead network can keep fine refinement locked out.
_DEFAULT_STARTUP_TIMEOUT_SECONDS = 30.0


class MPRWidget(QWidget):
    """Display the three panes managed by an :class:`MPRController`.

    The widget can enable async slicing because each pane has a ``QtViewer``
    that delivers completed slice responses. Model-only controllers remain
    synchronous by default. Per-pane async covers navigation only:
    draw-driven level refinement runs off the GUI thread only when napari's
    global experimental async setting is on (e.g. ``NAPARI_ASYNC=1`` in the
    environment before importing napari); without it, the first fine-level
    read after coarse-locked construction blocks the first post-show draw.

    When the controller's streaming session reports incomplete startup,
    construction enables async slicing immediately but keeps the panes locked
    to the coarsest level until startup completes (or ``startup_timeout_s``
    expires), so speculative fine reads cannot starve the coarse fill.

    Left-drag moves the linked cursor without panning the canvas. Modified
    drags and other mouse buttons leave the cursor unchanged; wheel zoom stays
    enabled.

    Parameters
    ----------
    controller : MPRController
        Model-level controller providing the three linked panes.
    parent : QWidget, optional
        Parent Qt widget.
    async_slicing : bool or None
        Enable async slicing after all pane ``QtViewer`` instances exist.
        ``None`` (the default) follows the controller's ``async_slicing``
        preference, enabling unless the controller was constructed with an
        explicit ``False``; a boolean here overrides that preference.
    startup_timeout_s : float
        Maximum seconds to hold the coarse-level lock waiting for streaming
        startup before unlocking fine refinement anyway. The default is
        30 seconds.
    """

    _progressive_arrival = Signal(int, object)
    _startup_arrival = Signal()

    def __init__(
        self,
        controller: MPRController,
        parent: QWidget | None = None,
        *,
        async_slicing: bool | None = None,
        startup_timeout_s: float = _DEFAULT_STARTUP_TIMEOUT_SECONDS,
    ) -> None:
        if async_slicing is not None and not isinstance(async_slicing, bool):
            raise TypeError('async_slicing must be a boolean or None')
        if isinstance(startup_timeout_s, bool) or not isinstance(
            startup_timeout_s, Real
        ):
            raise TypeError('startup_timeout_s must be a non-negative number')
        startup_timeout_s = float(startup_timeout_s)
        if not isfinite(startup_timeout_s) or startup_timeout_s < 0:
            raise ValueError('startup_timeout_s must be a non-negative number')
        super().__init__(parent)
        self.controller = controller
        self._close_callbacks: list[Callable[[], None]] = []
        self._qt_viewers: tuple[QtViewer, ...] = ()
        self._closed = False
        self._async_startup_pending = False
        self._startup_session: MPRStreamingSession | None = None
        self._startup_callback = self._on_startup_complete
        self._startup_timeout_timer = QTimer(self)
        self._startup_timeout_timer.setInterval(
            round(startup_timeout_s * 1000)
        )
        self._startup_timeout_timer.setSingleShot(True)
        self._startup_timeout_timer.timeout.connect(self._on_startup_timeout)
        self._startup_arrival.connect(self._finish_async_startup_if_complete)
        self._mouse_callbacks: list[
            tuple[ViewerModel, Callable[..., Generator[None, None, None]]]
        ] = []
        self._progressive_refresh_timer = QTimer(self)
        self._progressive_refresh_timer.setInterval(100)
        self._progressive_refresh_timer.setSingleShot(True)
        self._progressive_refresh_timer.timeout.connect(
            self._refresh_progressive_layers
        )
        self._progressive_arrival.connect(self._schedule_progressive_refresh)
        self._pending_progressive_panes: set[int] = set()
        self._progressive_callback = self._on_progressive_update

        enable_async = (
            async_slicing
            if async_slicing is not None
            else controller.async_slicing is not False
        )
        # QtViewer draws before connecting its async slice consumer. Keep those
        # construction-time draws on cached coarse data, then unlock only after
        # every consumer exists and async slicing is active — or, when a
        # streaming session is still filling its coarse data, once startup
        # completes. The finally block guarantees a construction failure never
        # leaves the controller's layers locked to coarse; unlocking after a
        # failed enable (it returns False when the private slicer API drifted)
        # restores the status quo.
        keep_coarse_lock = False
        try:
            if enable_async:
                controller.lock_to_coarsest()
            splitter = QSplitter(Qt.Orientation.Horizontal, self)
            pane_widgets = []
            qt_viewers = []
            for pane_index, (title, pane) in enumerate(
                zip(controller.pane_names, controller.panes, strict=True)
            ):
                pane_widget = QGroupBox(title, splitter)
                pane_layout = QVBoxLayout(pane_widget)
                pane_layout.setContentsMargins(0, 0, 0, 0)
                qt_viewer = QtViewer(pane)
                pane.scene.camera.mouse_pan = False
                pane_layout.addWidget(qt_viewer)
                splitter.addWidget(pane_widget)
                splitter.setStretchFactor(pane_index, 1)

                callback = partial(self._set_cursor_from_mouse, pane_index)
                pane.mouse_drag_callbacks.append(callback)
                self._mouse_callbacks.append((pane, callback))
                pane_widgets.append(pane_widget)
                qt_viewers.append(qt_viewer)
            controller.equalize_zoom()

            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(splitter)

            self._splitter = splitter
            self._pane_widgets = tuple(pane_widgets)
            self._qt_viewers = tuple(qt_viewers)
            if enable_async:
                keep_coarse_lock = self._enable_or_defer_async_slicing()
        finally:
            if enable_async and not keep_coarse_lock:
                self._disconnect_startup_wait()
                controller.unlock_levels()
        controller.on_progressive_update.append(self._progressive_callback)
        self.setWindowTitle('Multiplanar reconstruction')

    @property
    def qt_viewers(self) -> tuple[QtViewer, ...]:
        """One viewer per controller pane, in pane order; empty once closed."""
        return self._qt_viewers

    def add_close_callback(self, callback: Callable[[], None]) -> None:
        """Run ``callback`` on close, before the controller closes.

        Callbacks run in reverse registration order, so a decoration attached
        later is released before the ones it may depend on.
        """
        if not callable(callback):
            raise TypeError('close callback must be callable')
        self._close_callbacks.append(callback)

    def _enable_or_defer_async_slicing(self) -> bool:
        # Async slicing starts immediately in every case: the coarse-level
        # lock alone suppresses speculative fine reads, and deferring the
        # enable too would leave the panes slicing synchronously on the GUI
        # thread for the whole tier-zero fill. A failed enable (closed
        # controller or drifted slicer API) skips the deferral so the
        # constructor's finally block restores the unlocked status quo
        # rather than holding a synchronous widget at coarse.
        session = self.controller.streaming
        if not self.controller.enable_async_slicing():
            return False
        if session is None or session.startup_complete:
            return False

        self._startup_session = session
        self._async_startup_pending = True
        session.on_startup_complete.append(self._startup_callback)
        # Completion can race the property check above and callback
        # registration. Rechecking closes both missed-notification windows.
        if session.startup_complete:
            self._finish_async_startup()
        else:
            self._startup_timeout_timer.start()
        return True

    def _on_startup_complete(self) -> None:
        """Marshal worker-thread startup completion through Qt."""
        if not self._closed and self._async_startup_pending:
            self._startup_arrival.emit()

    def _finish_async_startup_if_complete(self) -> None:
        session = self._startup_session
        if session is not None and session.startup_complete:
            self._finish_async_startup()

    def _on_startup_timeout(self) -> None:
        session = self._startup_session
        if session is not None and not session.startup_complete:
            logger.warning(
                'streaming startup did not complete within %.0f s (%s); '
                'unlocking fine refinement anyway',
                self._startup_timeout_timer.interval() / 1000,
                session.startup_status(),
            )
        self._finish_async_startup()

    def _finish_async_startup(self) -> None:
        if self._closed or not self._async_startup_pending:
            return
        self._async_startup_pending = False
        self._disconnect_startup_wait()
        self.controller.unlock_levels()

    def _disconnect_startup_wait(self) -> None:
        self._startup_timeout_timer.stop()
        session = self._startup_session
        self._startup_session = None
        if session is not None:
            # A session may clear its subscriber list on close(), so a
            # check-then-remove could race it; removal simply tolerates the
            # callback already being gone.
            with suppress(ValueError):
                session.on_startup_complete.remove(self._startup_callback)

    def _on_progressive_update(
        self, level: int, region: tuple[slice, ...]
    ) -> None:
        """Marshal a worker-thread arrival through Qt's queued signal."""
        self._progressive_arrival.emit(level, region)

    def _schedule_progressive_refresh(
        self, level: int, region: tuple[slice, ...]
    ) -> None:
        if self._closed:
            return
        level_shape = self.controller._field_source.level_shape(level)
        affected = {
            pane_index
            for pane_index in range(len(self.controller.panes))
            if len(
                range(
                    *region[
                        self.controller._spatial_axes[
                            self.controller._target_axis_for_pane[pane_index]
                        ]
                    ].indices(
                        level_shape[
                            self.controller._spatial_axes[
                                self.controller._target_axis_for_pane[
                                    pane_index
                                ]
                            ]
                        ]
                    )
                )
            )
            == 1
        }
        self._pending_progressive_panes.update(
            affected or range(len(self.controller.panes))
        )
        if not self._progressive_refresh_timer.isActive():
            self._progressive_refresh_timer.start()

    def _refresh_progressive_layers(self) -> None:
        if self._closed:
            return
        pane_indices = tuple(self._pending_progressive_panes)
        self._pending_progressive_panes.clear()
        for pane_index in pane_indices:
            self.controller._image_layers[pane_index].refresh()

    def _set_cursor_from_mouse(
        self, pane_index: int, _viewer: ViewerModel, event: Any
    ) -> Generator[None, None, None]:
        if event.button != 1 or event.modifiers:
            return
        active_layer = self.controller.panes[
            pane_index
        ].layers.selection.active
        if isinstance(active_layer, Shapes) and active_layer.mode.startswith(
            'add_'
        ):
            return
        self.controller.set_cursor_from_pane(pane_index, event.position)
        yield
        while event.type == 'mouse_move':
            self.controller.set_cursor_from_pane(pane_index, event.position)
            yield

    def closeEvent(self, event: Any) -> None:
        """Disconnect the controller and release all pane canvases."""
        if not self._closed:
            self._closed = True
            unlock_pending = self._async_startup_pending
            self._async_startup_pending = False
            self._disconnect_startup_wait()
            if unlock_pending:
                self.controller.unlock_levels()
            with suppress(TypeError, RuntimeError):
                self._startup_arrival.disconnect(
                    self._finish_async_startup_if_complete
                )
            if (
                self._progressive_callback
                in self.controller.on_progressive_update
            ):
                self.controller.on_progressive_update.remove(
                    self._progressive_callback
                )
            self._progressive_refresh_timer.stop()
            self._pending_progressive_panes.clear()
            callbacks = self._close_callbacks
            self._close_callbacks = []
            for callback in reversed(callbacks):
                # Keep releasing the controller and canvases if one fails.
                try:
                    callback()
                except Exception:
                    logger.exception('MPRWidget close callback failed')
            for pane, callback in self._mouse_callbacks:
                if callback in pane.mouse_drag_callbacks:
                    pane.mouse_drag_callbacks.remove(callback)
            self._mouse_callbacks.clear()
            self.controller.close()
            qt_viewers = self._qt_viewers
            self._qt_viewers = ()
            for qt_viewer in qt_viewers:
                qt_viewer.close()
        super().closeEvent(event)
