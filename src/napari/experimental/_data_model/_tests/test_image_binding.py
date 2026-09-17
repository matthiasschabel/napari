import gc
import sys
import weakref

import numpy as np
import pytest

from napari.components import LayerList
from napari.experimental._data_model import (
    ArraySource,
    CoordinateAxis,
    DataObject,
    Field,
    ImageBinding,
    StructuredGridDomain,
)


class ViewerLike:
    def __init__(self):
        self.layers = LayerList()

    def add_layer(self, layer):
        self.layers.append(layer)


def image_object():
    return DataObject(
        'image',
        StructuredGridDomain((CoordinateAxis('y', 4), CoordinateAxis('x', 5))),
        {'value': Field('value', ArraySource(np.arange(20).reshape(4, 5)))},
    )


def test_two_images_keep_independent_presentation_and_explicit_scientific_updates():
    viewer = ViewerLike()
    obj = image_object()
    first = ImageBinding(viewer, obj, 'value')
    second = ImageBinding(viewer, obj, 'value')
    first.layer.opacity = 0.4
    first.layer.contrast_limits = (2, 15)
    second.layer.colormap = 'magma'
    first.layer.data = np.zeros((4, 5))
    np.testing.assert_array_equal(second.layer.data[2:3, 3:5], [[13, 14]])
    values = np.full((4, 5), 42)
    source = ArraySource(values)
    obj.update(fields={'value': Field('value', source)})
    for binding in (first, second):
        np.testing.assert_array_equal(binding.layer.data[2:3, 3:5], [[42, 42]])
    assert first.layer.opacity == pytest.approx(0.4, abs=1e-12)
    np.testing.assert_allclose(
        first.layer.contrast_limits, [2, 15], rtol=0, atol=1e-12
    )
    assert second.layer.colormap.name == 'magma'
    values[2, 3] = 64
    source.invalidate()
    first.refresh()
    np.testing.assert_array_equal(first.layer.data[2:3, 3:4], [[64]])
    viewer.layers.remove(first.layer)
    assert first.closed
    obj.update(name='renamed scientific object')
    assert not second.closed
    second.dispose()
    second.dispose()
    with pytest.raises(RuntimeError, match='closed'):
        second.refresh()


@pytest.mark.parametrize(
    'values', [np.zeros((4, 5), dtype=complex), np.full((4, 5), 'text')]
)
def test_invalid_projection_keeps_last_valid_consumer_and_notifies_others(
    values,
):
    viewer = ViewerLike()
    obj = image_object()
    binding = ImageBinding(viewer, obj, 'value')
    previous = binding.layer.data
    notified = []
    disconnect = obj.subscribe(lambda model: notified.append(model.revision))
    with pytest.raises(ExceptionGroup, match='subscriber failed'):
        obj.update(fields={'value': Field('value', ArraySource(values))})
    assert binding.layer.data is previous
    assert notified == [1]
    with pytest.raises(ExceptionGroup, match='subscriber failed') as rejected:
        obj.update(name='still unsupported')
    assert (
        "ImageBinding field 'value'"
        in rejected.value.exceptions[0].__notes__[0]
    )
    assert notified == [1, 2]
    obj.update(
        fields={'value': Field('value', ArraySource(np.full((4, 5), 58)))}
    )
    assert notified == [1, 2, 3]
    assert not binding.closed
    np.testing.assert_array_equal(binding.layer.data[1:2, 2:3], [[58]])
    binding.dispose()
    disconnect()


def test_binding_and_removed_layer_do_not_anchor_viewer_or_source_consumers():
    viewer = ViewerLike()
    obj = image_object()
    binding = ImageBinding(viewer, obj, 'value')
    layer = weakref.ref(binding.layer)
    owner = weakref.ref(binding)
    viewer.layers.clear()
    viewer_ref = weakref.ref(viewer)
    del binding, viewer
    gc.collect()
    assert owner() is None
    assert layer() is None
    assert viewer_ref() is None
    viewer = ViewerLike()
    obj.update(name='still usable')
    with (
        pytest.raises(ValueError, match='test failure'),
        ImageBinding(viewer, obj, 'value') as scoped,
    ):
        raise ValueError('test failure')
    assert scoped.closed


def test_dropping_binding_disconnects_without_retaining_it_in_the_model():
    viewer = ViewerLike()
    obj = image_object()
    binding = ImageBinding(viewer, obj, 'value')
    layer = binding.layer
    previous = layer.data
    reference = weakref.ref(binding)
    del binding
    gc.collect()
    assert reference() is None
    obj.update(
        fields={'value': Field('value', ArraySource(np.full((4, 5), 73)))}
    )
    assert layer.data is previous


def test_invalid_binding_has_no_layer_and_rank_change_preserves_last_projection():
    viewer = ViewerLike()
    with pytest.raises(TypeError, match='structured-grid'):
        ImageBinding(viewer, None, 'value')
    assert not viewer.layers
    obj = image_object()
    binding = ImageBinding(viewer, obj, 'value')
    previous = binding.layer.data
    new_domain = StructuredGridDomain(
        (
            CoordinateAxis('z', 2),
            CoordinateAxis('y', 4),
            CoordinateAxis('x', 5),
        )
    )
    with pytest.raises(ExceptionGroup, match='subscriber failed'):
        obj.update(
            domain=new_domain,
            fields={'value': Field('value', ArraySource(np.ones((2, 4, 5))))},
        )
    assert binding.layer.data is previous
    binding.dispose()


def test_binding_projects_changed_placement_and_preserves_consumer_metadata():
    from napari.experimental._data_model import (
        CoordinateEmbedding,
        CoordinateFrame,
    )

    viewer = ViewerLike()
    obj = image_object()
    binding = ImageBinding(viewer, obj, 'value')
    metadata = binding.layer.metadata
    metadata['local'] = 'consumer'
    frame = CoordinateFrame('scanner', (('y', 'mm'), ('x', 'mm')))
    obj.update(
        embeddings=(
            CoordinateEmbedding(obj.domain, frame, [[2, 0], [0, 3]], [4, 5]),
        )
    )
    np.testing.assert_allclose(binding.layer.scale, [2, 3], rtol=0, atol=1e-12)
    np.testing.assert_allclose(
        binding.layer.translate, [4, 5], rtol=0, atol=1e-12
    )
    assert binding.layer.metadata is metadata
    assert metadata['local'] == 'consumer'
    assert binding.layer.metadata['napari:data_object'] is obj
    binding.dispose()


def test_binding_rejects_single_axis_images_before_layer_construction():
    viewer = ViewerLike()
    obj = DataObject(
        'line',
        StructuredGridDomain((CoordinateAxis('x', 3),)),
        {'value': Field('value', ArraySource(np.ones(3)))},
    )
    with pytest.raises(NotImplementedError, match='two storage axes'):
        ImageBinding(viewer, obj, 'value')
    assert not viewer.layers


def test_binding_refresh_retains_bounded_volume_reads():
    class Source:
        shape = (100, 16, 16)
        dtype = np.dtype(float)
        levels = 1

        def __init__(self):
            self.reads = []

        def level_shape(self, level):
            return self.shape

        def read(self, region, *, level=0):
            shape = tuple(
                len(range(*item.indices(size)))
                for item, size in zip(region, self.shape, strict=True)
            )
            assert shape != self.shape
            self.reads.append(region)
            return np.ones(shape)

    source = Source()
    domain = StructuredGridDomain(
        tuple(
            CoordinateAxis(name, size)
            for name, size in zip(('z', 'y', 'x'), source.shape, strict=True)
        )
    )
    obj = DataObject('volume', domain, {'value': Field('value', source)})
    viewer = ViewerLike()
    binding = ImageBinding(viewer, obj, 'value')
    binding.refresh()
    np.testing.assert_allclose(
        binding.layer.data[40:41, 2:4, 3:5],
        np.ones((1, 2, 2)),
        rtol=0,
        atol=1e-12,
    )
    assert source.reads
    binding.dispose()


@pytest.mark.skipif(
    'pytestqt.plugin' not in sys.modules,
    reason='requires the Qt test plugin and native display',
)
def test_closing_one_real_viewer_disposes_only_its_binding(qtbot, monkeypatch):
    import napari.settings as settings
    from napari import Viewer
    from napari.settings import NapariSettings

    monkeypatch.setattr(
        settings, '_SETTINGS', NapariSettings(config_path=None)
    )
    obj = image_object()
    first = Viewer(show=False)
    second = Viewer(show=False)
    try:
        one = ImageBinding(first, obj, 'value')
        two = ImageBinding(second, obj, 'value')
        first.close()
        assert one.closed
        assert not two.closed
        obj.update(
            fields={'value': Field('value', ArraySource(np.full((4, 5), 27)))}
        )
        np.testing.assert_array_equal(two.layer.data[1:2, 2:3], [[27]])
        second.close()
        assert two.closed
    finally:
        first.close()
        second.close()


def test_plain_list_owner_release_closes_binding_and_rejects_reuse():
    viewer = ViewerLike()
    viewer.layers = []
    obj = image_object()
    binding = ImageBinding(viewer, obj, 'value')
    viewer.layers.clear()
    gc.collect()
    assert binding.closed
    with pytest.raises(RuntimeError, match='no longer exists'):
        _ = binding.layer
    with pytest.raises(RuntimeError, match='closed'), binding:
        pass


@pytest.mark.parametrize('labels', [False, True])
def test_binding_rejects_multiscale_or_labels_before_insertion(labels):
    from napari.experimental._data_model import Interpolation, MultiscaleSource

    viewer = ViewerLike()
    obj = image_object()
    source = (
        ArraySource(np.ones((4, 5), dtype=int))
        if labels
        else MultiscaleSource((np.ones((4, 5)), np.ones((2, 2))))
    )
    obj.update(
        fields={
            'value': Field(
                'value',
                source,
                interpolation=Interpolation.NEAREST
                if labels
                else Interpolation.LINEAR,
            )
        }
    )
    with pytest.raises(NotImplementedError, match='single-level scalar Image'):
        ImageBinding(viewer, obj, 'value')
    assert not viewer.layers


def test_empty_update_is_rejected_before_mutating_the_image():
    viewer = ViewerLike()
    obj = image_object()
    binding = ImageBinding(viewer, obj, 'value')
    previous = binding.layer.data
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 0), CoordinateAxis('x', 5))
    )
    with pytest.raises(ExceptionGroup, match='subscriber failed'):
        obj.update(
            domain=domain,
            fields={'value': Field('value', ArraySource(np.empty((0, 5))))},
        )
    assert binding.layer.data is previous
    binding.dispose()


def test_binding_connection_failure_removes_its_layer_and_subscription():
    from types import SimpleNamespace

    class RejectingEmitter:
        def connect(self, callback):
            raise RuntimeError('connection rejected')

        def disconnect(self, callback):
            pass

    class Layers(list):
        events = SimpleNamespace(removed=RejectingEmitter())

    viewer = ViewerLike()
    viewer.layers = Layers()
    obj = image_object()
    with pytest.raises(RuntimeError, match='connection rejected'):
        ImageBinding(viewer, obj, 'value')
    assert not viewer.layers
    obj.update(fields={})


def test_binding_updates_axis_metadata_and_interpolation():
    from napari.experimental._data_model import Interpolation

    viewer = ViewerLike()
    domain = StructuredGridDomain(
        (
            CoordinateAxis(
                'time', 3, unit='s', values=np.array([0.0, 1.0, 3.0])
            ),
            CoordinateAxis('x', 4, unit='mm'),
        )
    )
    obj = DataObject(
        'data', domain, {'value': Field('value', ArraySource(np.ones((3, 4))))}
    )
    binding = ImageBinding(viewer, obj, 'value')
    assert 'napari:irregular_axes' in binding.layer.metadata
    regular = StructuredGridDomain(
        (
            CoordinateAxis(
                'delay', 3, unit='ms', values=np.array([0.0, 2.0, 4.0])
            ),
            CoordinateAxis('position', 4, unit='cm'),
        )
    )
    obj.update(
        domain=regular,
        fields={
            'value': Field(
                'value',
                ArraySource(np.ones((3, 4))),
                interpolation=Interpolation.NEAREST,
            )
        },
    )
    assert binding.layer.interpolation2d == 'nearest'
    assert binding.layer.axis_labels == ('delay', 'position')
    assert tuple(str(unit) for unit in binding.layer.units) == (
        'millisecond',
        'centimeter',
    )
    assert 'napari:irregular_axes' not in binding.layer.metadata
    binding.dispose()
