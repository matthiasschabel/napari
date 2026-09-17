from __future__ import annotations

import gc
import importlib.util
import subprocess
import sys
from time import monotonic, sleep

import numpy as np
import pytest

from napari.experimental._data_model import (
    MPRController,
    ROICollection,
    synthetic_mri,
)

_QT_BINDINGS = ('PyQt5', 'PyQt6', 'PySide2', 'PySide6')
_QT_AVAILABLE = importlib.util.find_spec('qtpy') is not None and any(
    importlib.util.find_spec(binding) is not None for binding in _QT_BINDINGS
)
requires_qt = pytest.mark.skipif(
    not _QT_AVAILABLE, reason='Qt bindings are unavailable'
)


@requires_qt
def test_mpr_widget_honors_controller_async_slicing_false(
    qtbot, headless_vispy
) -> None:
    from napari.experimental._data_model._mpr_qt import MPRWidget

    controller = MPRController(
        synthetic_mri(), async_slicing=False
    )
    widget = MPRWidget(controller)
    qtbot.addWidget(widget)

    assert controller.async_slicing_active is False
    assert all(
        slicer._force_sync is True for slicer in controller._pane_slicers
    )
    widget.close()


@requires_qt
def test_mpr_widget_rejects_non_boolean_async_slicing(qtbot) -> None:
    from napari.experimental._data_model._mpr_qt import MPRWidget

    controller = MPRController(synthetic_mri())
    try:
        with pytest.raises(TypeError, match='async_slicing must be a boolean'):
            MPRWidget(controller, async_slicing=1)  # type: ignore[arg-type]
    finally:
        controller.close()


@requires_qt
@pytest.mark.parametrize('bad_timeout', ['30', True, None])
def test_mpr_widget_rejects_non_numeric_startup_timeout(
    qtbot, bad_timeout
) -> None:
    from napari.experimental._data_model._mpr_qt import MPRWidget

    controller = MPRController(synthetic_mri())
    try:
        with pytest.raises(
            TypeError, match='startup_timeout_s must be a non-negative number'
        ):
            MPRWidget(controller, startup_timeout_s=bad_timeout)
    finally:
        controller.close()


@requires_qt
@pytest.mark.parametrize('bad_timeout', [-1.0, float('inf'), float('nan')])
def test_mpr_widget_rejects_out_of_range_startup_timeout(
    qtbot, bad_timeout
) -> None:
    from napari.experimental._data_model._mpr_qt import MPRWidget

    controller = MPRController(synthetic_mri())
    try:
        with pytest.raises(
            ValueError, match='startup_timeout_s must be a non-negative number'
        ):
            MPRWidget(controller, startup_timeout_s=bad_timeout)
    finally:
        controller.close()


@requires_qt
def test_mpr_widget_coalesces_many_progressive_arrivals(
    qtbot, headless_vispy, monkeypatch
) -> None:
    from napari.experimental._data_model._mpr_qt import MPRWidget

    controller = MPRController(synthetic_mri())
    widget = MPRWidget(controller, async_slicing=False)
    qtbot.addWidget(widget)
    refreshes: list[int] = []

    def record_refresh(layer, *args, **kwargs) -> None:
        if layer in controller._image_layers:
            refreshes.append(controller._image_layers.index(layer))

    monkeypatch.setattr(
        type(controller._image_layers[0]), 'refresh', record_refresh
    )
    region = (slice(0, 1),) * len(controller._field_source.shape)
    for _index in range(50):
        controller._on_progressive_arrival(0, region)

    qtbot.waitUntil(lambda: len(refreshes) >= 3, timeout=1000)
    qtbot.wait(150)
    assert sorted(refreshes) == [0, 1, 2]
    widget.close()


def test_data_model_package_import_does_not_import_qt() -> None:
    code = """
import sys

import napari.experimental._data_model

qt_bindings = ('PyQt5', 'PyQt6', 'PySide2', 'PySide6')
loaded = [
    name
    for name in sys.modules
    if any(name == binding or name.startswith(f'{binding}.') for binding in qt_bindings)
]
assert not loaded, loaded
"""

    result = subprocess.run(
        [sys.executable, '-c', code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.fixture
def mpr_widget(qtbot, headless_vispy):
    from qtpy.QtCore import QEvent
    from qtpy.QtWidgets import QApplication

    from napari._qt.qt_viewer import QtViewer
    from napari.experimental._data_model._mpr_qt import MPRWidget

    data_object = synthetic_mri()
    controller = MPRController(
        data_object, roi_collection=ROICollection(data_object)
    )
    widget = MPRWidget(controller)
    qtbot.addWidget(widget)
    yield controller, widget
    widget.close()
    widget.deleteLater()
    deadline = monotonic() + 2
    while True:
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        gc.collect()
        if not QtViewer._instances or monotonic() >= deadline:
            break
        sleep(0.001)
    leaked = len(QtViewer._instances)
    assert not leaked
    QtViewer._instances.clear()


@requires_qt
def test_mpr_widget_constructs_three_visible_titled_panes(
    qtbot, mpr_widget
) -> None:
    from napari._qt.qt_viewer import QtViewer

    controller, widget = mpr_widget
    widget.resize(1200, 500)
    with qtbot.waitExposed(widget):
        widget.show()

    qt_viewers = widget.findChildren(QtViewer)
    assert len(qt_viewers) == 3
    assert all(qt_viewer.isVisibleTo(widget) for qt_viewer in qt_viewers)
    assert (
        tuple(pane_widget.title() for pane_widget in widget._pane_widgets)
        == controller.pane_names
    )
    assert len({pane.scene.camera.zoom for pane in controller.panes}) == 1


@requires_qt
def test_mpr_widget_mouse_callback_respects_active_draw_layer(
    mpr_widget,
) -> None:
    from napari.utils._test_utils import read_only_mouse_event
    from napari.utils.interactions import (
        mouse_move_callbacks,
        mouse_press_callbacks,
        mouse_release_callbacks,
    )

    controller, _widget = mpr_widget
    source_pane = controller.panes[0]
    initial_cursor = controller.cursor.copy()
    initial_z = initial_cursor[0]
    press_position = np.asarray(source_pane.dims.point)
    press_position[4:6] = (-1.0, 14.0)
    move_position = press_position.copy()
    move_position[4:6] = (2.6, 19.4)

    draw_layer = controller.begin_roi_draw(0)
    assert source_pane.layers.selection.active is draw_layer
    mouse_press_callbacks(
        source_pane,
        read_only_mouse_event(
            type='mouse_press',
            button=1,
            position=tuple(press_position),
            modifiers=[],
        ),
    )
    np.testing.assert_array_equal(controller.cursor, initial_cursor)

    source_pane.layers.selection.active = source_pane.layers[0]
    mouse_press_callbacks(
        source_pane,
        read_only_mouse_event(
            type='mouse_press',
            button=1,
            position=tuple(press_position),
            modifiers=[],
        ),
    )
    try:
        np.testing.assert_allclose(
            controller.cursor,
            (initial_z, -1.0, 14.0),
            rtol=0.0,
            atol=1e-12,
        )
        assert controller.panes[1].dims.current_step[4] == 10
        assert controller.panes[2].dims.current_step[5] == 10

        mouse_move_callbacks(
            source_pane,
            read_only_mouse_event(
                type='mouse_move',
                is_dragging=True,
                position=tuple(move_position),
                modifiers=[],
            ),
        )

        np.testing.assert_allclose(
            controller.cursor,
            (initial_z, 2.6, 19.4),
            rtol=0.0,
            atol=1e-12,
        )
        assert controller.panes[1].dims.current_step[4] == 14
        assert controller.panes[2].dims.current_step[5] == 16
    finally:
        mouse_release_callbacks(
            source_pane,
            read_only_mouse_event(
                type='mouse_release',
                button=1,
                position=tuple(move_position),
                modifiers=[],
            ),
        )


@requires_qt
def test_mpr_widget_uses_cursor_drag_interaction(mpr_widget) -> None:
    controller, widget = mpr_widget

    assert all(
        qt_viewer.canvas.view.camera.mouse_pan is False
        for qt_viewer in widget._qt_viewers
    )
    assert all(
        pane.scene.camera.mouse_zoom is True for pane in controller.panes
    )


@requires_qt
@pytest.mark.parametrize(('button', 'modifiers'), [(2, []), (1, ['Shift'])])
def test_mpr_widget_ignores_other_drag_gestures(
    mpr_widget, button, modifiers
) -> None:
    from napari.utils._test_utils import read_only_mouse_event
    from napari.utils.interactions import mouse_press_callbacks

    controller, _widget = mpr_widget
    source_pane = controller.panes[0]
    initial_cursor = controller.cursor.copy()
    position = np.asarray(source_pane.dims.point)
    position[4:6] = (2.6, 19.4)

    mouse_press_callbacks(
        source_pane,
        read_only_mouse_event(
            type='mouse_press',
            button=button,
            position=tuple(position),
            modifiers=modifiers,
        ),
    )

    np.testing.assert_array_equal(controller.cursor, initial_cursor)


@requires_qt
def test_mpr_widget_close_disconnects_and_deletes_cleanly(
    mpr_widget,
) -> None:
    controller, widget = mpr_widget
    images = [pane.layers[0] for pane in controller.panes]
    original_limits = tuple(images[1].contrast_limits)
    original_step = controller.panes[1].dims.current_step[1]
    new_step = 0 if original_step != 0 else 1
    callbacks = tuple(widget._mouse_callbacks)

    widget.close()

    assert all(
        callback not in pane.mouse_drag_callbacks
        for pane, callback in callbacks
    )
    images[0].contrast_limits = (0.1, 0.4)
    controller.panes[0].dims.set_current_step(1, new_step)
    assert tuple(images[1].contrast_limits) == original_limits
    assert controller.panes[1].dims.current_step[1] == original_step


