from dataclasses import FrozenInstanceError
from types import MappingProxyType
from typing import Any

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
    MultiscaleSource,
    StructuredGridDomain,
)
from napari.experimental._data_model._source import source_level_shards


class LazyArray:
    def __init__(self, data: np.ndarray) -> None:
        self.shape = data.shape
        self.dtype = data.dtype
        self._data = data
        self.array_calls = 0
        self.reads = 0

    def __array__(
        self, dtype: Any = None, copy: bool | None = None
    ) -> np.ndarray:
        self.array_calls += 1
        return np.asarray(self._data, dtype=dtype)

    def __getitem__(self, region: tuple[slice, ...]) -> np.ndarray:
        self.reads += 1
        return self._data[region]


class ChunkedArray(LazyArray):
    def __init__(self, data: np.ndarray, chunks: tuple[int, ...]) -> None:
        super().__init__(data)
        self.chunks = chunks


class CountingSource:
    def __init__(self, data: np.ndarray, *, levels: int = 1) -> None:
        self.shape = data.shape
        self.dtype = data.dtype
        self.levels = levels
        self._data = data
        self.read_calls = 0
        self.last_level = -1

    def level_shape(self, level: int) -> tuple[int, ...]:
        return self.shape

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        self.read_calls += 1
        self.last_level = level
        return self._data[region]


def make_domain(shape: tuple[int, ...]) -> StructuredGridDomain:
    return StructuredGridDomain(
        tuple(
            CoordinateAxis(f'axis_{index}', size)
            for index, size in enumerate(shape)
        )
    )


def test_array_source_does_not_materialize_lazy_input_at_construction() -> (
    None
):
    lazy = LazyArray(np.arange(12).reshape(3, 4))
    source = ArraySource(lazy)

    assert source.shape == (3, 4)
    assert source.dtype == np.dtype(np.int64)
    assert source.levels == 1
    assert source.level_shape(0) == (3, 4)
    assert source.level_chunks(0) is None
    assert lazy.array_calls == 0
    assert lazy.reads == 0

    result = source.read((slice(1, 2), slice(2, 4)))

    np.testing.assert_array_equal(result, np.array([[6, 7]]))
    assert lazy.reads == 1


def test_array_source_accepts_an_in_memory_sequence() -> None:
    source = ArraySource([[1, 2], [3, 4]])

    np.testing.assert_array_equal(
        source.read((slice(None), slice(None))),
        np.array([[1, 2], [3, 4]]),
    )


def test_array_source_rejects_invalid_region_type() -> None:
    source = ArraySource(np.ones((2, 2)))

    with pytest.raises(TypeError, match='tuple of slices'):
        source.read((slice(None), 0))  # type: ignore[arg-type]


def test_array_source_rejects_wrong_region_dimension() -> None:
    source = ArraySource(np.ones((2, 2)))

    with pytest.raises(ValueError, match='dimensionality'):
        source.read((slice(None),))


@pytest.mark.parametrize(
    ('region', 'bound_name'),
    [
        ((slice(-1, 1), slice(None)), 'start'),
        ((slice(None), slice(None, 3)), 'stop'),
    ],
)
def test_array_source_rejects_out_of_bounds_region(
    region: tuple[slice, ...], bound_name: str
) -> None:
    source = ArraySource(np.ones((2, 2)))

    with pytest.raises(ValueError, match=rf'slice {bound_name}.*outside'):
        source.read(region)


def test_array_source_rejects_negative_region_step() -> None:
    source = ArraySource(np.ones((2, 2)))

    with pytest.raises(ValueError, match='negative steps'):
        source.read((slice(None, None, -1), slice(None)))


def test_array_source_rejects_nonzero_level() -> None:
    source = ArraySource(np.ones((2, 2)))

    with pytest.raises(IndexError, match='outside the available levels'):
        source.read((slice(None), slice(None)), level=1)
    with pytest.raises(IndexError, match='outside the available levels'):
        source.level_shape(-1)


def test_array_source_rejects_non_integer_level() -> None:
    source = ArraySource(np.ones((2, 2)))

    with pytest.raises(TypeError, match='resolution level must be an integer'):
        source.level_shape(True)  # type: ignore[arg-type]


def test_array_source_rejects_storage_without_indexing() -> None:
    class NonIndexableArray:
        shape = (2,)
        dtype = np.dtype(float)

    with pytest.raises(TypeError, match='support region indexing'):
        ArraySource(NonIndexableArray())


def test_array_source_rejects_invalid_shape_or_dtype() -> None:
    class InvalidArray:
        shape = (object(),)
        dtype = np.dtype(float)

        def __getitem__(self, region: tuple[slice, ...]) -> np.ndarray:
            return np.empty(0)

    with pytest.raises(TypeError, match='valid shape and dtype'):
        ArraySource(InvalidArray())


def test_multiscale_source_reads_each_level_in_its_own_index_space() -> None:
    finest = LazyArray(np.arange(64).reshape(4, 4, 4))
    coarsest = LazyArray(np.arange(8).reshape(2, 2, 2) + 100)
    source = MultiscaleSource((finest, coarsest))

    assert source.shape == (4, 4, 4)
    assert source.level_shape(0) == (4, 4, 4)
    assert source.level_shape(1) == (2, 2, 2)
    assert source.levels == 2
    assert source.level_chunks(0) is None
    assert finest.reads == coarsest.reads == 0

    region = (slice(0, 2), slice(1, 2), slice(0, 1))
    np.testing.assert_array_equal(
        source.read(region, level=1),
        np.array([[[102]], [[106]]]),
    )
    assert finest.reads == 0
    assert coarsest.reads == 1


def test_multiscale_source_exposes_regular_level_chunks() -> None:
    level = ChunkedArray(np.zeros((4, 6), dtype=np.uint8), (2, 3))
    source = MultiscaleSource((level,))

    assert source.level_chunks(0) == (2, 3)


def test_multiscale_source_rejects_chunk_dimensionality_mismatch() -> None:
    level = ChunkedArray(np.zeros((4, 6), dtype=np.uint8), (2,))
    source = MultiscaleSource((level,))

    with pytest.raises(ValueError, match='dimensionality must match'):
        source.level_chunks(0)


def test_multiscale_source_rejects_inconsistent_dtypes() -> None:
    with pytest.raises(TypeError, match='consistent dtype'):
        MultiscaleSource(
            (
                np.zeros((4, 4), dtype=np.uint8),
                np.zeros((2, 2), dtype=np.uint16),
            )
        )


def test_multiscale_source_rejects_increasing_shapes() -> None:
    with pytest.raises(ValueError, match='monotonically non-increasing'):
        MultiscaleSource((np.zeros((4, 2)), np.zeros((2, 3))))


def test_multiscale_source_level_shape_checks_bounds() -> None:
    source = MultiscaleSource((np.zeros((4, 4)), np.zeros((2, 2))))

    with pytest.raises(IndexError, match='outside the available levels'):
        source.level_shape(2)


def test_multiscale_source_uses_selected_level_for_region_bounds() -> None:
    source = MultiscaleSource((np.zeros((4, 4)), np.zeros((2, 2))))

    with pytest.raises(ValueError, match=r'slice stop 3.*axis 0.*size 2'):
        source.read((slice(None, 3), slice(None)), level=1)


def test_data_object_construction_never_reads_field_data() -> None:
    data = np.arange(12).reshape(3, 4)
    source = CountingSource(data)
    model_field = Field('signal', source, unit='mV')

    data_object = DataObject(
        'acquisition',
        make_domain(data.shape),
        {'signal': model_field},
    )

    assert data_object.fields['signal'] is model_field
    assert source.read_calls == 0


def test_complex_dtype_is_first_class() -> None:
    values = np.ones((2, 3), dtype=np.complex64) * (1 + 2j)
    model_field = Field('signal', ArraySource(values), unit='mV')
    data_object = DataObject(
        'complex image',
        make_domain(values.shape),
        {'signal': model_field},
    )

    assert data_object.fields['signal'].dtype == np.dtype(np.complex64)
    np.testing.assert_array_equal(
        model_field.source.read((slice(None), slice(None))), values
    )


def test_component_axis_may_be_interleaved_with_domain_axes() -> None:
    domain = make_domain((2, 4))
    vector = Field(
        'vector',
        ArraySource(np.zeros((2, 3, 4))),
        component_axes=(1,),
    )

    data_object = DataObject('vectors', domain, {'vector': vector})

    assert data_object.fields['vector'].domain_storage_axes == (0, 2)


def test_field_units_are_independent_of_axis_units() -> None:
    domain = StructuredGridDomain((CoordinateAxis('x', 2, unit='um'),))
    temperature = Field('temperature', ArraySource(np.ones(2)), unit='K')

    data_object = DataObject('sample', domain, {'temperature': temperature})

    assert data_object.domain.axes[0].unit == 'um'
    assert data_object.fields['temperature'].unit == 'K'


def test_zero_field_object_without_embedding_is_valid() -> None:
    data_object = DataObject('bare domain', make_domain((2, 3)))

    assert data_object.fields == {}
    assert data_object.embeddings == ()


@pytest.mark.parametrize(
    'domain',
    [
        type(
            'WrongShapeDomain',
            (),
            {
                'shape': [2],
                'intrinsic_frame': CoordinateFrame(
                    'intrinsic', (('x', None),)
                ),
            },
        )(),
        type(
            'WrongFrameDomain',
            (),
            {'shape': (2,), 'intrinsic_frame': 'intrinsic'},
        )(),
    ],
)
def test_data_object_validates_domain_attribute_types(domain: object) -> None:
    with pytest.raises(TypeError, match=r'domain\.(shape|intrinsic_frame)'):
        DataObject('invalid domain', domain)  # type: ignore[arg-type]


def test_field_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match='field name must not be empty'):
        Field('', ArraySource(np.ones(2)))


def test_unit_parser_type_error_identifies_the_invalid_unit():
    with pytest.raises(ValueError, match=r'field unit.*not pint-compatible'):
        Field(
            'signal', ArraySource(np.ones(2)), unit='seconds since 1970-01-01'
        )


def test_field_rejects_invalid_source() -> None:
    with pytest.raises(TypeError, match='does not satisfy DataSource'):
        Field('signal', object())  # type: ignore[arg-type]


def test_field_rejects_non_callable_read_attribute() -> None:
    class NonCallableReadSource:
        shape = (2,)
        dtype = np.dtype(float)
        levels = 1

        def level_shape(self, level: int) -> tuple[int, ...]:
            return self.shape

        @property
        def read(self) -> None:
            return None

    with pytest.raises(TypeError, match='read attribute must be callable'):
        Field('signal', NonCallableReadSource())  # type: ignore[arg-type]


def test_field_rejects_non_callable_level_shape_attribute() -> None:
    class NonCallableLevelShapeSource:
        shape = (2,)
        dtype = np.dtype(float)
        levels = 1
        level_shape = (2,)

        def read(
            self, region: tuple[slice, ...], *, level: int = 0
        ) -> np.ndarray:
            return np.ones(2)[region]

    with pytest.raises(
        TypeError, match='level_shape attribute must be callable'
    ):
        Field('signal', NonCallableLevelShapeSource())  # type: ignore[arg-type]


def test_field_rejects_invalid_source_shape_or_dtype() -> None:
    class InvalidSource:
        shape = (object(),)
        dtype = np.dtype(float)
        levels = 1

        def level_shape(self, level: int) -> tuple[int, ...]:
            return self.shape

        def read(
            self, region: tuple[slice, ...], *, level: int = 0
        ) -> np.ndarray:
            return np.empty(0)

    with pytest.raises(TypeError, match='invalid shape or dtype'):
        Field('signal', InvalidSource())


def test_field_rejects_invalid_interpolation() -> None:
    with pytest.raises(TypeError, match='must be an Interpolation'):
        Field(
            'signal',
            ArraySource(np.ones(2)),
            interpolation='linear',  # type: ignore[arg-type]
        )


def test_field_carries_interpolation_and_missing_value() -> None:
    model_field = Field(
        'labels',
        ArraySource(np.array([0, -1, 2])),
        interpolation=Interpolation.NEAREST,
        missing_value=-1,
    )

    assert model_field.interpolation is Interpolation.NEAREST
    assert model_field.missing_value == -1


def test_field_rejects_invalid_level_count() -> None:
    with pytest.raises(ValueError, match='positive integer'):
        Field('signal', CountingSource(np.ones(2), levels=0))


def test_field_rejects_level_zero_shape_mismatch() -> None:
    source = CountingSource(np.ones(2))
    source.level_shape = lambda level: (1,)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match='level-zero shape must equal shape'):
        Field('signal', source)


def test_field_rejects_non_integer_component_axis() -> None:
    with pytest.raises(TypeError, match='component axes must be integers'):
        Field(
            'signal',
            ArraySource(np.ones((2, 3))),
            component_axes=(1.5,),  # type: ignore[arg-type]
        )


def test_field_rejects_duplicate_component_axes() -> None:
    with pytest.raises(ValueError, match='must be unique'):
        Field(
            'signal',
            ArraySource(np.ones((2, 3))),
            component_axes=(1, 1),
        )


def test_field_rejects_out_of_range_component_axis() -> None:
    with pytest.raises(ValueError, match='outside the source shape'):
        Field(
            'signal',
            ArraySource(np.ones((2, 3))),
            component_axes=(2,),
        )


def test_field_read_passes_level_to_source() -> None:
    source = CountingSource(np.arange(2), levels=2)
    model_field = Field('signal', source)

    result = model_field.read((slice(None),), level=1)

    np.testing.assert_array_equal(result, np.arange(2))
    assert source.last_level == 1


def test_data_object_rejects_field_axis_count_mismatch() -> None:
    domain = make_domain((2, 3))
    field_with_component_only = Field(
        'rgb',
        ArraySource(np.ones((2, 3))),
        component_axes=(1,),
    )

    with pytest.raises(ValueError, match='non-component axes'):
        DataObject('image', domain, {'rgb': field_with_component_only})


def test_data_object_rejects_field_shape_mismatch() -> None:
    domain = make_domain((2, 3))
    model_field = Field('signal', ArraySource(np.ones((2, 4))))

    with pytest.raises(ValueError, match='does not match domain shape'):
        DataObject('image', domain, {'signal': model_field})


def test_data_object_rejects_mismatched_field_key() -> None:
    model_field = Field('signal', ArraySource(np.ones(2)))

    with pytest.raises(ValueError, match='key must match'):
        DataObject('image', make_domain((2,)), {'magnitude': model_field})


def test_data_object_rejects_non_field_mapping_value() -> None:
    with pytest.raises(TypeError, match='Field instances'):
        DataObject('image', make_domain((2,)), {'signal': object()})  # type: ignore[dict-item]


def test_data_object_copies_ordered_fields_and_metadata() -> None:
    first = Field('first', ArraySource(np.ones(2)))
    second = Field('second', ArraySource(np.ones(2)))
    fields = {'first': first, 'second': second}
    metadata = {'modality': 'MRI'}

    data_object = DataObject(
        'image', make_domain((2,)), fields, metadata=metadata
    )
    fields.clear()
    metadata.clear()

    assert list(data_object.fields) == ['first', 'second']
    assert data_object.metadata == {'modality': 'MRI'}


def test_data_object_accepts_an_ordered_mapping() -> None:
    model_field = Field('signal', ArraySource(np.ones(2)))
    fields = MappingProxyType({'signal': model_field})

    data_object = DataObject('image', make_domain((2,)), fields)  # type: ignore[arg-type]

    assert data_object.fields == {'signal': model_field}


def test_data_object_rejects_embedding_for_another_domain() -> None:
    first = make_domain((2,))
    second = make_domain((2,))
    scanner = CoordinateFrame('scanner', (('x', 'mm'),))
    embedding = CoordinateEmbedding(first, scanner, [[1.0]])

    with pytest.raises(ValueError, match='this domain intrinsic frame'):
        DataObject('image', second, embeddings=[embedding])


def test_data_object_rejects_invalid_domain() -> None:
    with pytest.raises(TypeError, match='Domain protocol'):
        DataObject('image', object())  # type: ignore[arg-type]


def test_data_object_rejects_non_mapping_fields() -> None:
    with pytest.raises(TypeError, match='ordered mapping'):
        DataObject('image', make_domain((2,)), fields=[])  # type: ignore[arg-type]


def test_data_object_rejects_non_sequence_embeddings() -> None:
    with pytest.raises(TypeError, match='must be a sequence'):
        DataObject('image', make_domain((2,)), embeddings=object())  # type: ignore[arg-type]


def test_data_object_rejects_non_embedding_list_value() -> None:
    with pytest.raises(TypeError, match='CoordinateEmbedding instances'):
        DataObject('image', make_domain((2,)), embeddings=[object()])  # type: ignore[list-item]


def test_data_object_rejects_non_dictionary_metadata() -> None:
    with pytest.raises(TypeError, match='metadata must be a dictionary'):
        DataObject('image', make_domain((2,)), metadata=[])  # type: ignore[arg-type]


def test_field_and_data_object_compare_and_hash_by_identity() -> None:
    values = np.ones(2)
    first_field = Field('signal', ArraySource(values))
    second_field = Field('signal', ArraySource(values))
    domain = make_domain((2,))
    first_object = DataObject('image', domain, {'signal': first_field})
    second_object = DataObject('image', domain, {'signal': first_field})

    assert first_field != second_field
    assert len({first_field, second_field}) == 2
    assert first_object != second_object
    assert len({first_object, second_object}) == 2


def test_data_object_attributes_are_frozen() -> None:
    data_object = DataObject('image', make_domain((2,)))

    with pytest.raises(FrozenInstanceError):
        data_object.name = 'renamed'  # type: ignore[misc]


def test_data_object_containers_are_immutable() -> None:
    model_field = Field('signal', ArraySource(np.ones(2)))
    data_object = DataObject(
        'image', make_domain((2,)), {'signal': model_field}
    )

    with pytest.raises(TypeError):
        data_object.fields['other'] = model_field  # type: ignore[index]
    with pytest.raises(AttributeError):
        data_object.embeddings.append(object())  # type: ignore[attr-defined]


def test_data_object_update_preserves_identity_and_publishes_valid_state() -> (
    None
):
    original = Field('signal', ArraySource(np.ones(2)))
    obj = DataObject('image', make_domain((2,)), {'signal': original})
    observed = []
    disconnect = obj.subscribe(
        lambda changed: observed.append(
            (changed, changed.revision, changed.domain.shape, changed.fields)
        )
    )

    with pytest.raises(ValueError, match='does not match domain shape'):
        obj.update(domain=make_domain((3,)))
    assert obj.revision == 0
    assert obj.fields['signal'] is original
    assert observed == []

    replacement = Field('signal', ArraySource(np.arange(3)))
    obj.update(domain=make_domain((3,)), fields={'signal': replacement})
    assert observed == [(obj, 1, (3,), {'signal': replacement})]
    fork = obj.replace(name='fork')
    assert fork is not obj
    assert fork.fields['signal'] is replacement
    assert fork.revision == 0
    disconnect()
    disconnect()
    obj.update(name='renamed')
    assert len(observed) == 1


def test_dataclass_replacement_cannot_silently_discard_fields() -> None:
    from dataclasses import replace

    model_field = Field('signal', ArraySource(np.ones(2)))
    obj = DataObject('image', make_domain((2,)), {'signal': model_field})
    with pytest.raises(TypeError):
        replace(obj, name='renamed')


def test_data_object_notification_failure_commits_and_notifies_other_consumers() -> (
    None
):
    obj = DataObject('image', make_domain((2,)))
    observed = []
    obj.subscribe(lambda changed: changed.update(name='reentrant'))
    obj.subscribe(lambda changed: observed.append(changed.name))
    with pytest.raises(ExceptionGroup) as error:
        obj.update(name='committed')
    assert isinstance(error.value.exceptions[0], RuntimeError)
    assert observed == ['committed']
    assert obj.name == 'committed'
    assert obj.revision == 1
    with pytest.raises(TypeError, match='subscriber must be callable'):
        obj.subscribe(None)


def test_copy_and_pickle_do_not_copy_consumer_subscriptions() -> None:
    import copy
    import pickle

    obj = DataObject('image', make_domain((2,)))
    observed = []
    obj.subscribe(lambda changed: observed.append(changed.name))
    for copied in (
        copy.copy(obj),
        copy.deepcopy(obj),
        pickle.loads(pickle.dumps(obj)),
    ):
        copied.update(name='copy')
    assert observed == []


def test_subscriptions_have_independent_lifetimes_during_notification():
    obj = DataObject('image', make_domain((2,)))
    observed = []

    def first(changed):
        observed.append('first')

    obj.subscribe(first)
    obj.subscribe(lambda changed: observed.append('second'))
    unsubscribe_duplicate = obj.subscribe(first)
    unsubscribe_duplicate()
    unsubscribe_duplicate()
    obj.update()
    assert obj.revision == 0
    with pytest.raises(TypeError):
        obj.update(unknown=True)
    assert obj.revision == 0
    obj.update(name='renamed')
    assert observed == ['first', 'second']

    obj = DataObject('image', make_domain((2,)))
    observed.clear()

    def close_other_consumer(changed):
        unsubscribe_other()
        changed.subscribe(lambda next_change: observed.append('late'))

    obj.subscribe(close_other_consumer)
    unsubscribe_other = obj.subscribe(
        lambda changed: observed.append('closed')
    )
    obj.update(name='first update')
    assert observed == []
    obj.update(name='second update')
    assert observed == ['late']


class ShardedArray(ChunkedArray):
    def __init__(
        self,
        data: np.ndarray,
        chunks: tuple[int, ...],
        shards: tuple[int, ...],
    ) -> None:
        super().__init__(data, chunks)
        self.shards = shards


def test_multiscale_source_exposes_level_shards() -> None:
    level = ShardedArray(np.zeros((16, 16), dtype=np.uint8), (4, 4), (8, 8))
    source = MultiscaleSource((level,))

    assert source.level_shards(0) == (8, 8)
    assert source_level_shards(source, 0) == (8, 8)


def test_multiscale_source_reports_no_shards_when_unsharded() -> None:
    level = ChunkedArray(np.zeros((16, 16), dtype=np.uint8), (4, 4))
    source = MultiscaleSource((level,))

    assert source.level_shards(0) is None
    assert source_level_shards(source, 0) is None


def test_multiscale_source_rejects_shard_dimensionality_mismatch() -> None:
    level = ShardedArray(np.zeros((16, 16), dtype=np.uint8), (4, 4), (8,))
    source = MultiscaleSource((level,))

    with pytest.raises(ValueError, match='shard dimensionality must match'):
        source.level_shards(0)


def test_source_level_shards_of_a_source_without_the_method() -> None:
    source = ArraySource(np.zeros((4, 4), dtype=np.uint8))

    assert source_level_shards(source, 0) is None


@pytest.mark.parametrize('pyramid', [False, True])
def test_array_read_rejects_announced_change_during_indexing(pyramid):
    from napari.experimental._data_model import (
        MultiscaleSource,
        SourceChangedError,
    )

    class ChangingArray:
        shape = (2,)
        dtype = np.dtype('int64')

        def __getitem__(self, region):
            source.invalidate()
            return np.arange(2)[region]

    array = ChangingArray()
    source = MultiscaleSource((array,)) if pyramid else ArraySource(array)
    with pytest.raises(SourceChangedError, match='while reading'):
        source.read((slice(None),))
    assert source.revision == 1
