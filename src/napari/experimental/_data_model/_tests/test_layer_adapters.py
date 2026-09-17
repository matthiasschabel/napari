from __future__ import annotations

from copy import deepcopy
from typing import Any

import dask.array as da
import numpy as np
import pytest

from napari.experimental._data_model import (
    ArraySource,
    CoordinateAxis,
    CoordinateEmbedding,
    CoordinateFrame,
    DataObject,
    Field,
    Interpolation,
    Polygon,
    PolygonSetDomain,
    StructuredGridDomain,
    data_object_from_layer,
    layer_from_data_object,
)
from napari.layers import Image, Labels

_TRANSFORM_RTOL = 1e-12
_TRANSFORM_ATOL = 1e-12


def test_layer_from_data_object_requires_structured_grid_domain() -> None:
    polygon = Polygon(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]))
    data_object = DataObject(
        'geometry', PolygonSetDomain((polygon,), ('x', 'y'))
    )

    with pytest.raises(TypeError, match='requires a StructuredGridDomain'):
        layer_from_data_object(data_object)


def assert_layers_equivalent(original: Image | Labels, restored: Any) -> None:
    assert type(restored) is type(original)
    assert restored.data is original.data
    assert restored.name == original.name
    assert restored.axis_labels == original.axis_labels
    assert len(restored.units) == len(original.units)
    assert all(
        restored_unit == original_unit
        for restored_unit, original_unit in zip(
            restored.units, original.units, strict=True
        )
    )
    assert restored.metadata == original.metadata
    np.testing.assert_allclose(
        restored.scale,
        original.scale,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )
    np.testing.assert_allclose(
        restored.translate,
        original.translate,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )
    np.testing.assert_allclose(
        restored.rotate,
        original.rotate,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )
    np.testing.assert_allclose(
        restored.shear,
        original.shear,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )
    np.testing.assert_allclose(
        restored.affine.affine_matrix,
        original.affine.affine_matrix,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )
    np.testing.assert_allclose(
        restored._data_to_world.affine_matrix,
        original._data_to_world.affine_matrix,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )
    if isinstance(original, Image):
        assert restored.rgb is original.rgb


@pytest.mark.parametrize('shape', [(3, 4), (2, 2, 2, 3, 4)])
def test_image_round_trip_preserves_nd_data(shape: tuple[int, ...]) -> None:
    data = np.arange(np.prod(shape)).reshape(shape).astype(np.float32)
    options = {'name': 'signal', 'metadata': {'modality': 'MRI'}, 'rgb': False}
    layer = Image(data, **options)

    data_object = data_object_from_layer(layer)
    restored = layer_from_data_object(data_object)

    assert_layers_equivalent(layer, restored)
    assert data_object.fields['data'].source.array is data
    assert data_object.fields['data'].dtype == data.dtype


def test_complex_data_object_cannot_be_projected_to_image() -> None:
    data = np.ones((2, 3), dtype=np.complex64) * (1 + 0.5j)
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    data_object = DataObject(
        'complex signal',
        domain,
        {'signal': Field('signal', ArraySource(data))},
    )

    with pytest.raises(
        NotImplementedError,
        match=r'Image layers do not support complex data.*magnitude or phase',
    ):
        layer_from_data_object(data_object)


def test_rgb_image_round_trip_uses_trailing_component_axis() -> None:
    data = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)
    layer = Image(data, name='rgb image', rgb=True)

    data_object = data_object_from_layer(layer)
    restored = layer_from_data_object(data_object)

    assert data_object.domain.shape == (4, 5)
    assert data_object.fields['data'].shape == (4, 5, 3)
    assert data_object.fields['data'].component_axes == (2,)
    assert_layers_equivalent(layer, restored)


def test_labels_round_trip_preserves_scale_translate_and_metadata() -> None:
    data = np.arange(12, dtype=np.uint16).reshape(3, 4)
    layer = Labels(
        data,
        name='segmentation',
        scale=(2.5, 0.75),
        translate=(-10.0, 4.0),
        metadata={'reviewed': True},
    )

    data_object = data_object_from_layer(layer)
    restored = layer_from_data_object(data_object)

    assert data_object.fields['data'].interpolation is Interpolation.NEAREST
    assert data_object.metadata['napari:layer']['kind'] == 'labels'
    assert_layers_equivalent(layer, restored)


def test_image_round_trip_preserves_full_composed_affine() -> None:
    data = np.arange(20, dtype=np.float32).reshape(4, 5)
    affine = np.array([[1.2, 0.1, 7.0], [-0.15, 0.9, -3.0], [0.0, 0.0, 1.0]])
    layer = Image(
        data,
        name='oriented',
        scale=(1.5, 0.8),
        translate=(-2.0, 5.0),
        rotate=27.0,
        shear=(0.2,),
        affine=affine,
    )

    data_object = data_object_from_layer(layer)
    embedding = data_object.embeddings[0]
    restored = layer_from_data_object(data_object)

    np.testing.assert_allclose(
        embedding.matrix,
        layer._data_to_world.linear_matrix,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )
    np.testing.assert_allclose(
        embedding.offset,
        layer._data_to_world.translate,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )
    assert_layers_equivalent(layer, restored)


def test_image_round_trip_preserves_axis_labels_and_units() -> None:
    layer = Image(
        np.zeros((2, 3, 4), dtype=np.float32),
        axis_labels=('z', 'y', 'x'),
        units=('um', 'nm', 'mm'),
        name='calibrated',
    )

    data_object = data_object_from_layer(layer)
    restored = layer_from_data_object(data_object)

    assert tuple(axis.name for axis in data_object.domain.axes) == (
        'z',
        'y',
        'x',
    )
    assert tuple(axis.unit for axis in data_object.domain.axes) == (
        'micrometer',
        'nanometer',
        'millimeter',
    )
    assert_layers_equivalent(layer, restored)


def test_reserved_metadata_key_round_trip_preserves_original_value() -> None:
    original_value = {'existing': 'scientific metadata'}
    layer = Image(np.zeros((2, 2)), metadata={'napari:layer': original_value})

    restored = layer_from_data_object(data_object_from_layer(layer))

    assert restored.metadata == {'napari:layer': original_value}


def test_duplicate_axis_labels_use_model_fallback_and_round_trip() -> None:
    layer = Image(np.zeros((2, 3)), axis_labels=('position', 'position'))

    data_object = data_object_from_layer(layer)
    restored = layer_from_data_object(data_object)

    assert tuple(axis.name for axis in data_object.domain.axes) == (
        'dim_0',
        'dim_1',
    )
    assert restored.axis_labels == layer.axis_labels


class CountingDaskProxy:
    def __init__(self, array: da.Array) -> None:
        self._array = array
        self.shape = array.shape
        self.dtype = array.dtype
        self.ndim = array.ndim
        self.chunks = array.chunks
        self.getitem_keys: list[Any] = []
        self.compute_calls = 0

    def __getitem__(self, region: Any) -> Any:
        self.getitem_keys.append(region)
        return self._array[region]

    def compute(self, **kwargs: Any) -> np.ndarray:
        self.compute_calls += 1
        return self._array.compute(**kwargs)


def _is_full_array_read(key: Any, ndim: int) -> bool:
    items = list(key if isinstance(key, tuple) else (key,))
    if Ellipsis in items:
        ellipsis = items.index(Ellipsis)
        missing = ndim - (len(items) - 1)
        items[ellipsis : ellipsis + 1] = [slice(None)] * missing
    items.extend([slice(None)] * (ndim - len(items)))
    return len(items) == ndim and all(
        isinstance(item, slice)
        and item.start is None
        and item.stop is None
        and item.step is None
        for item in items
    )


def test_dask_backed_image_round_trip_reads_only_display_regions() -> None:
    proxy = CountingDaskProxy(
        da.arange(120, chunks=12).reshape((4, 5, 6)).rechunk((2, 5, 3))
    )
    layer = Image(proxy, name='lazy')
    proxy.getitem_keys.clear()
    proxy.compute_calls = 0

    data_object = data_object_from_layer(layer)

    assert proxy.getitem_keys == []
    assert proxy.compute_calls == 0

    restored = layer_from_data_object(data_object)

    assert restored.data is proxy
    assert proxy.getitem_keys
    assert all(
        not _is_full_array_read(key, proxy.ndim) for key in proxy.getitem_keys
    )
    assert proxy.compute_calls == 0


def test_data_object_adapter_rejects_non_layer_input() -> None:
    with pytest.raises(TypeError, match='Image or Labels'):
        data_object_from_layer(object())  # type: ignore[arg-type]


def test_layer_adapter_rejects_non_data_object_input() -> None:
    with pytest.raises(TypeError, match='must be a DataObject'):
        layer_from_data_object(object())  # type: ignore[arg-type]


def test_layer_adapter_rejects_non_array_source() -> None:
    class OtherSource:
        shape = (2, 3)
        dtype = np.dtype(np.float32)
        levels = 1

        def level_shape(self, level: int) -> tuple[int, ...]:
            return self.shape

        def read(
            self, region: tuple[slice, ...], *, level: int = 0
        ) -> np.ndarray:
            return np.ones(self.shape, dtype=self.dtype)[region]

    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    data_object = DataObject(
        'other source', domain, {'data': Field('data', OtherSource())}
    )

    with pytest.raises(NotImplementedError, match='not backed by ArraySource'):
        layer_from_data_object(data_object)


def _valid_layer_marker(
    shape: tuple[int, ...], *, kind: str = 'image', rgb: bool = False
) -> dict[str, Any]:
    marker = deepcopy(
        data_object_from_layer(Image(np.zeros(shape))).metadata['napari:layer']
    )
    marker['kind'] = kind
    marker['rgb'] = rgb
    return marker


def _data_object_with_marker(
    model_field: Field,
    domain: StructuredGridDomain,
    marker: dict[str, Any],
) -> DataObject:
    return DataObject(
        'marked',
        domain,
        {model_field.name: model_field},
        metadata={'napari:layer': marker},
    )


@pytest.mark.parametrize(
    ('key', 'value'), [('kind', 'points'), ('rgb', 'yes')]
)
def test_layer_adapter_rejects_malformed_marker_kind_or_rgb(
    key: str, value: Any
) -> None:
    data = np.ones((2, 3))
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    marker = _valid_layer_marker(data.shape)
    marker[key] = value
    data_object = _data_object_with_marker(
        Field('data', ArraySource(data)), domain, marker
    )

    with pytest.raises(ValueError, match=r'image/labels kind.*boolean rgb'):
        layer_from_data_object(data_object)


@pytest.mark.parametrize('missing', ['transform', 'axis_labels'])
def test_layer_adapter_rejects_incomplete_marker(missing: str) -> None:
    data = np.ones((2, 3))
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    marker = _valid_layer_marker(data.shape)
    del marker[missing]
    data_object = _data_object_with_marker(
        Field('data', ArraySource(data)), domain, marker
    )

    with pytest.raises(ValueError, match='metadata is incomplete'):
        layer_from_data_object(data_object)


def test_foreign_napari_layer_dictionary_is_not_an_adapter_marker() -> None:
    data = np.ones((2, 3))
    foreign_marker = {'kind': 'scientific acquisition', 'rgb': 'unknown'}
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    data_object = DataObject(
        'foreign metadata',
        domain,
        {'data': Field('data', ArraySource(data))},
        metadata={'napari:layer': foreign_marker},
    )

    layer = layer_from_data_object(data_object)

    assert layer.metadata['napari:layer'] == foreign_marker


def test_layer_adapter_rejects_labels_field_with_components() -> None:
    data = np.ones((2, 3, 3), dtype=np.uint8)
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    model_field = Field('data', ArraySource(data), component_axes=(2,))
    data_object = _data_object_with_marker(
        model_field,
        domain,
        _valid_layer_marker(domain.shape, kind='labels'),
    )

    with pytest.raises(ValueError, match='Labels fields cannot have'):
        layer_from_data_object(data_object)


def test_layer_adapter_rejects_non_trailing_rgb_component_axis() -> None:
    data = np.ones((3, 2, 3), dtype=np.uint8)
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    model_field = Field('data', ArraySource(data), component_axes=(0,))
    data_object = _data_object_with_marker(
        model_field,
        domain,
        _valid_layer_marker(domain.shape, rgb=True),
    )

    with pytest.raises(ValueError, match='one trailing component axis'):
        layer_from_data_object(data_object)


def test_layer_adapter_rejects_non_rgb_components() -> None:
    data = np.ones((2, 3, 2), dtype=np.float32)
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    model_field = Field('data', ArraySource(data), component_axes=(2,))
    data_object = _data_object_with_marker(
        model_field, domain, _valid_layer_marker(domain.shape)
    )

    with pytest.raises(NotImplementedError, match='non-RGB field component'):
        layer_from_data_object(data_object)


def test_layer_adapter_requires_field_name_when_selection_is_ambiguous() -> (
    None
):
    domain = StructuredGridDomain((CoordinateAxis('x', 2),))
    data_object = DataObject(
        'two fields',
        domain,
        {
            'first': Field('first', ArraySource(np.ones(2))),
            'second': Field('second', ArraySource(np.ones(2))),
        },
    )

    with pytest.raises(ValueError, match='field must be specified'):
        layer_from_data_object(data_object)


def test_layer_adapter_rejects_unknown_field_name() -> None:
    domain = StructuredGridDomain((CoordinateAxis('x', 2),))
    data_object = DataObject(
        'one field', domain, {'data': Field('data', ArraySource(np.ones(2)))}
    )

    with pytest.raises(KeyError, match="unknown field 'missing'"):
        layer_from_data_object(data_object, field='missing')


def test_hand_built_regular_data_object_derives_layer_scale() -> None:
    data = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    domain = StructuredGridDomain(
        (
            CoordinateAxis('z', 2, unit='mm', values=np.array([10.0, 12.0])),
            CoordinateAxis(
                'y', 3, unit='mm', values=np.array([20.0, 21.5, 23.0])
            ),
            CoordinateAxis(
                'x',
                4,
                unit='mm',
                values=np.array([30.0, 30.5, 31.0, 31.5]),
            ),
        )
    )
    data_object = DataObject(
        'MRI block', domain, {'signal': Field('signal', ArraySource(data))}
    )

    layer = layer_from_data_object(data_object, field='signal')

    assert isinstance(layer, Image)
    assert layer.data is data
    assert layer.axis_labels == ('z', 'y', 'x')
    assert tuple(str(unit) for unit in layer.units) == (
        'millimeter',
        'millimeter',
        'millimeter',
    )
    np.testing.assert_allclose(
        layer.scale, [2.0, 1.5, 0.5], rtol=0.0, atol=_TRANSFORM_ATOL
    )
    np.testing.assert_allclose(
        layer.translate,
        [10.0, 20.0, 30.0],
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )


def test_hand_built_irregular_data_object_is_not_regularized() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('z', 3, values=np.array([0.0, 1.0, 3.0]), unit='mm'),)
    )
    data_object = DataObject(
        'irregular',
        domain,
        {'signal': Field('signal', ArraySource(np.ones(3)))},
    )

    with pytest.raises(NotImplementedError, match='irregular coordinate'):
        layer_from_data_object(data_object)


def test_markerless_non_numeric_axis_values_are_not_regularized() -> None:
    domain = StructuredGridDomain(
        (
            CoordinateAxis(
                'sample',
                2,
                values=np.array(['first', 'second']),
                dtype_kind='categorical',
            ),
        )
    )
    data_object = DataObject(
        'categorical',
        domain,
        {'data': Field('data', ArraySource(np.ones(2)))},
    )

    with pytest.raises(NotImplementedError, match='non-numeric coordinate'):
        layer_from_data_object(data_object)


def test_markerless_zero_axis_spacing_is_rejected() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('x', 2, values=np.array([1.0, 1.0])),)
    )
    data_object = DataObject(
        'zero spacing',
        domain,
        {'data': Field('data', ArraySource(np.ones(2)))},
    )

    with pytest.raises(NotImplementedError, match='zero coordinate spacing'):
        layer_from_data_object(data_object)


def test_marked_object_does_not_interpret_axis_values_as_transform() -> None:
    data = np.ones((2, 3))
    domain = StructuredGridDomain(
        (
            CoordinateAxis('y', 2, values=np.array([0.0, 3.0])),
            CoordinateAxis('x', 3, values=np.array([0.0, 1.0, 4.0])),
        )
    )
    data_object = _data_object_with_marker(
        Field('data', ArraySource(data)),
        domain,
        _valid_layer_marker(domain.shape),
    )

    layer = layer_from_data_object(data_object)

    np.testing.assert_allclose(layer.scale, [1.0, 1.0])
    np.testing.assert_allclose(layer.translate, [0.0, 0.0])


def test_multiscale_layer_is_not_silently_flattened() -> None:
    layer = Image(
        [np.ones((8, 8)), np.ones((4, 4))], multiscale=True, name='pyramid'
    )

    with pytest.raises(NotImplementedError, match='multiscale'):
        data_object_from_layer(layer)


def test_non_world_embedding_is_not_silently_dropped() -> None:
    domain = StructuredGridDomain((CoordinateAxis('x', 2),))
    scanner = CoordinateFrame('scanner', (('x', None),))
    embedding = CoordinateEmbedding(domain, scanner, [[1.0]])
    data_object = DataObject(
        'placed',
        domain,
        {'signal': Field('signal', ArraySource(np.ones(2)))},
        embeddings=[embedding],
    )

    with pytest.raises(NotImplementedError, match="not the supported 'world'"):
        layer_from_data_object(data_object)


def test_multiple_embeddings_are_not_silently_dropped() -> None:
    domain = StructuredGridDomain((CoordinateAxis('x', 2),))
    world = CoordinateFrame('world', (('x', None),))
    first = CoordinateEmbedding(domain, world, [[1.0]])
    second = CoordinateEmbedding(domain, world, [[2.0]])
    data_object = DataObject(
        'placed twice',
        domain,
        {'signal': Field('signal', ArraySource(np.ones(2)))},
        embeddings=(first, second),
    )

    with pytest.raises(NotImplementedError, match=r'multiple.*embeddings'):
        layer_from_data_object(data_object)


def test_non_square_embedding_is_not_silently_projected() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    world = CoordinateFrame('world', (('distance', None),))
    embedding = CoordinateEmbedding(domain, world, [[1.0, 0.0]])
    data_object = DataObject(
        'projected',
        domain,
        {'signal': Field('signal', ArraySource(np.ones((2, 3))))},
        embeddings=(embedding,),
    )

    with pytest.raises(NotImplementedError, match='non-square'):
        layer_from_data_object(data_object)


def test_numeric_axis_values_and_world_embedding_are_ambiguous() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('x', 2, values=np.array([10.0, 12.0])),)
    )
    world = CoordinateFrame('world', (('x', 'mm'),))
    embedding = CoordinateEmbedding(domain, world, [[2.0]], [10.0])
    data_object = DataObject(
        'ambiguous',
        domain,
        {'signal': Field('signal', ArraySource(np.ones(2)))},
        embeddings=(embedding,),
    )

    with pytest.raises(NotImplementedError, match=r'ambiguous.*index space'):
        layer_from_data_object(data_object)


def test_markerless_round_trip_restores_composed_affine() -> None:
    layer = Image(
        np.arange(20, dtype=np.float32).reshape(4, 5),
        scale=(1.5, 0.8),
        translate=(-2.0, 5.0),
        rotate=27.0,
        shear=(0.2,),
    )
    converted = data_object_from_layer(layer)
    metadata = dict(converted.metadata)
    del metadata['napari:layer']
    markerless = DataObject(
        converted.name,
        converted.domain,
        converted.fields,
        embeddings=converted.embeddings,
        metadata=metadata,
    )

    restored = layer_from_data_object(markerless)

    np.testing.assert_allclose(
        restored._data_to_world.affine_matrix,
        layer._data_to_world.affine_matrix,
        rtol=_TRANSFORM_RTOL,
        atol=_TRANSFORM_ATOL,
    )


def test_export_uses_updated_embedding_instead_of_legacy_transform():
    layer = Image(np.zeros((3, 4)), scale=(2, 3), translate=(5, 7))
    obj = data_object_from_layer(layer)
    embedding = CoordinateEmbedding(
        obj.domain,
        obj.embeddings[0].target_frame,
        np.array([[1.0, 0.5], [0.0, 2.0]]),
        np.array([11.0, 13.0]),
    )
    obj.update(embeddings=[embedding])
    restored = layer_from_data_object(obj)
    np.testing.assert_allclose(
        restored.data_to_world((1, 2)), [13, 17], rtol=1e-12
    )
    assert restored.data is layer.data
    obj.update(embeddings=[])
    np.testing.assert_allclose(
        layer_from_data_object(obj).data_to_world((1, 2)), [1, 2], rtol=1e-12
    )


def test_readonly_labels_export_does_not_advertise_editing():
    data = np.zeros((3, 4), dtype=np.uint8)
    layer = Labels(data)
    obj = data_object_from_layer(layer)
    data.flags.writeable = False
    restored = layer_from_data_object(obj)
    assert not restored.editable
    assert restored.data is data
