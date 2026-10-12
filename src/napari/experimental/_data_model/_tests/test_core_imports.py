"""Import boundary of the data model, and the napari internals its adapters touch.

Model modules never import napari's viewer subsystems (layers, components, Qt,
vispy); shapely is optional and imported inside functions. Only the adapter
modules (``_mpr``, ``_mpr_qt``, ``_layer_adapters``, ``_image_binding``,
``_view_bridge``, ``_shapes_codec``) import the viewer, and the private napari
names they rely on are checked by ``test_adapter_private_touches_still_exist``
so an upstream sync that renames one fails here rather than at run time.
"""

import subprocess
import sys

import pytest


def test_scientific_core_constructs_with_units_without_viewer_or_geometry():
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            """
import builtins
import sys
original_import = builtins.__import__
blocked = ('napari.layers', 'napari.components', 'napari._qt', 'napari._vispy', 'shapely')
def guarded(name, *args, **kwargs):
    if any(name == prefix or name.startswith(prefix + '.') for prefix in blocked):
        raise AssertionError('unexpected scientific-core dependency: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
from napari.experimental._data_model import ArraySource, CoordinateAxis, DataObject, Field, StructuredGridDomain
import numpy as np
axis = CoordinateAxis('time', 2, unit='ms')
obj = DataObject('test', StructuredGridDomain((axis,)), {'x': Field('x', ArraySource(np.ones(2)), unit='mm')})
from napari.experimental._data_model import ImageBinding
assert ImageBinding is not None
assert not any(name in sys.modules for name in blocked)
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_mpr_and_roi_work_without_the_streaming_stack():
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            """
import builtins
import importlib.util
import sys
original_import = builtins.__import__
package = 'napari.experimental._data_model.'
blocked = tuple(package + name for name in (
    '_atlas', '_atlas_pane', '_atlas_vispy', '_bricks', '_caching_source',
    '_downsample', '_ladder', '_level_subset', '_mpr_streaming', '_ngff_adapter',
    '_prefetch', '_profile', '_progressive', '_working_set',
))
def guarded(name, *args, **kwargs):
    if any(
        name == prefix or name.startswith(prefix + '.') for prefix in blocked
    ):
        raise AssertionError('unexpected streaming dependency: ' + name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
from napari.experimental._data_model._measure import mean_over_roi
from napari.experimental._data_model._mpr import MPRController
from napari.experimental._data_model._mri import synthetic_mri
controller = MPRController(synthetic_mri())
assert controller.streaming is None
controller.close()
if importlib.util.find_spec('qtpy') is not None:
    import napari.experimental._data_model._mpr_qt
assert not any(name in sys.modules for name in blocked)
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_adapter_private_touches_still_exist() -> None:
    """Every private napari name the adapters use, in one place.

    ``_mpr``: ``ViewerModel._layer_slicer`` and ``_update_layers``,
    ``_LayerSlicer._force_sync``, ``Shapes._is_creating`` and the private
    polygon model. ``_layer_adapters``: ``Layer._data_to_world``.
    ``_mpr_qt``: ``QtViewer``.
    """
    pytest.importorskip('qtpy')
    from napari._qt.qt_viewer import QtViewer
    from napari.components import ViewerModel
    from napari.layers import Image, Shapes
    from napari.layers.shapes._shapes_models.polygon import Polygon

    viewer = ViewerModel()
    assert QtViewer is not None
    assert Polygon is not None
    assert callable(viewer._update_layers)
    assert hasattr(viewer._layer_slicer, '_force_sync')
    assert isinstance(Shapes._is_creating, property)
    assert isinstance(Image._data_to_world, property)
