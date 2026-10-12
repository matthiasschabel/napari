import numpy as np
import pytest

from napari.experimental._data_model import (
    ArraySource,
    CoordinateAxis,
    CoordinateEmbedding,
    CoordinateFrame,
    DataObject,
    Field,
    FieldGeometry,
    MultiscaleSource,
    SourceChangedError,
    StructuredGridDomain,
)


class RecordingSource(ArraySource):
    def __init__(self, data):
        super().__init__(data)
        self.regions = []

    def read(self, region, *, level=0):
        self.regions.append(region)
        return super().read(region, level=level)


def acquisition():
    signal = RecordingSource(np.arange(24).reshape(4, 6) * (1 + 2j))
    bvalues = RecordingSource(np.array([0, 500, 0, 1000]))
    frame = CoordinateFrame('scanner', (('x', 'mm'), ('y', 'mm'), ('z', 'mm')))
    tensor = FieldGeometry(
        'symmetric_tensor', basis_frame=frame, basis='orthonormal'
    )
    obj = DataObject(
        'diffusion',
        StructuredGridDomain(
            (CoordinateAxis('measurement', 4), CoordinateAxis('x', 6))
        ),
        {'signal': Field('signal', signal, geometry=FieldGeometry('complex'))},
        coordinates={
            'b_value': Field(
                'b_value', bvalues, unit='s/mm**2', dimensions=('measurement',)
            ),
            'b_matrix': Field(
                'b_matrix',
                ArraySource(np.arange(36).reshape(4, 3, 3)),
                unit='s/mm**2',
                dimensions=('measurement',),
                component_axes=(1, 2),
                geometry=tensor,
            ),
        },
    )
    return obj, signal, bvalues


def test_diffusion_reordering_is_lazy_and_preserves_paired_encodings():
    obj, signal, bvalues = acquisition()
    selected = obj.isel({'measurement': [2, 0, 2], 'x': slice(1, 5, 2)})
    assert signal.regions == bvalues.regions == []
    assert selected.domain.shape == (3, 2)
    assert (
        selected.coordinates['b_matrix'].geometry
        is obj.coordinates['b_matrix'].geometry
    )
    np.testing.assert_allclose(
        selected.fields['signal'].read((slice(None), slice(None))),
        (np.arange(24).reshape(4, 6) * (1 + 2j))[[2, 0, 2]][:, 1:5:2],
        atol=1e-12,
        rtol=0,
    )
    np.testing.assert_array_equal(
        selected.coordinates['b_value'].read((slice(None),)), [0, 0, 0]
    )
    np.testing.assert_array_equal(
        selected.coordinates['b_matrix'].read(
            (slice(None), slice(None), slice(None))
        ),
        np.arange(36).reshape(4, 3, 3)[[2, 0, 2]],
    )
    assert selected.coordinates['b_matrix'].dimensions == ('measurement',)
    assert selected.coordinates['b_matrix'].unit == 's/mm**2'
    assert obj.domain.shape == (4, 6)


def test_distant_selection_reads_only_requested_runs():
    source = RecordingSource(np.arange(10001))
    obj = DataObject(
        'events',
        StructuredGridDomain((CoordinateAxis('event', 10001),)),
        {'value': Field('value', source)},
    )
    result = obj.isel({'event': [10000, 0, 2, 3]})
    np.testing.assert_array_equal(
        result.fields['value'].read((slice(None),)), [10000, 0, 2, 3]
    )
    assert (
        sum(len(range(*region[0].indices(10001))) for region in source.regions)
        == 4
    )
    nested = result.isel({'event': slice(None, None, -1)})
    np.testing.assert_array_equal(
        nested.fields['value'].read((slice(1, 4, 2),)), [2, 10000]
    )


def test_coordinate_lookup_disambiguates_and_invalidates_selected_sources():
    obj, signal, bvalues = acquisition()
    with pytest.raises(ValueError, match='multiple observations'):
        obj.sel({'b_value': 0})
    result = obj.sel({'b_value': 0}, duplicates='all')
    assert result.domain.shape == (2, 6)
    assert signal.regions == []
    signal.invalidate()
    np.testing.assert_allclose(
        result.fields['signal'].read((slice(None), slice(None))),
        obj.fields['signal'].read((slice(None), slice(None)))[[0, 2]],
        atol=1e-12,
        rtol=0,
    )
    bvalues.invalidate()
    with pytest.raises(SourceChangedError, match='recreate'):
        result.fields['signal'].read((slice(None), slice(None)))
    with pytest.raises(SourceChangedError, match='recreate'):
        result.coordinates['b_matrix'].read(
            (slice(None), slice(None), slice(None))
        )


def test_microscopy_integer_timestamps_are_values_and_constraints_intersect():
    ticks = np.array([2**60, 2**60 + 1, 2**60 + 1], dtype=np.int64)
    obj = DataObject(
        'microscopy',
        StructuredGridDomain((CoordinateAxis('frame', 3),)),
        {'signal': Field('signal', ArraySource(np.array([10, 20, 30])))},
        coordinates={
            'time': Field(
                'time', ArraySource(ticks), unit='ns', dimensions=('frame',)
            ),
            'channel': Field(
                'channel',
                ArraySource(np.array([0, 0, 1])),
                dimensions=('frame',),
            ),
        },
    )
    selected = obj.sel({'time': 2**60 + 1, 'channel': 1})
    np.testing.assert_array_equal(
        selected.fields['signal'].read((slice(None),)), [30]
    )
    selected = obj.sel({'time': 2**60})
    np.testing.assert_array_equal(
        selected.fields['signal'].read((slice(None),)), [10]
    )
    np.testing.assert_array_equal(
        obj.isel({'frame': 1}).fields['signal'].read((slice(None),)), [20]
    )


def test_spatial_selection_updates_affine_and_rejects_irregular_gather():
    obj, _, _ = acquisition()
    frame = CoordinateFrame('world', (('x', 'mm'),))
    obj.update(
        embeddings=(CoordinateEmbedding(obj.domain, frame, [[0, 2]], [10]),)
    )
    result = obj.isel({'measurement': [3, 0, 2], 'x': slice(5, 0, -2)})
    np.testing.assert_allclose(
        result.embeddings[0].matrix, [[0, -4]], atol=1e-12, rtol=0
    )
    np.testing.assert_allclose(
        result.embeddings[0].offset, [20], atol=1e-12, rtol=0
    )
    with pytest.raises(NotImplementedError, match='affine'):
        obj.isel({'x': [0, 4, 1]})


@pytest.mark.parametrize(
    ('selector', 'error'),
    [
        (True, TypeError),
        ([1.5], TypeError),
        ([[1]], TypeError),
        (6, IndexError),
        ([-7], IndexError),
        (np.array([2**64 - 1], dtype=np.uint64), IndexError),
    ],
)
def test_invalid_positional_selection(selector, error):
    obj, _, _ = acquisition()
    with pytest.raises(error):
        obj.isel({'x': selector})


def test_empty_scalar_and_transposed_associations():
    obj = DataObject(
        'sample',
        StructuredGridDomain(
            (CoordinateAxis('x', 3, values=np.array([1.0, 3.0, 8.0])),)
        ),
        {
            'vector': Field(
                'vector',
                ArraySource(np.arange(6).reshape(2, 3)),
                dimensions=('x',),
                component_axes=(0,),
            ),
            'constant': Field(
                'constant', ArraySource(np.array(5)), dimensions=()
            ),
        },
    )
    empty = obj.isel({'x': []})
    assert empty.fields['vector'].read((slice(None), slice(None))).shape == (
        2,
        0,
    )
    assert empty.fields['constant'].read(()) == 5
    selected = obj.sel({'x': 3})
    np.testing.assert_array_equal(
        selected.fields['vector'].read((slice(None), slice(None))), [[1], [4]]
    )
    np.testing.assert_allclose(
        selected.domain.axes[0].values, [3.0], atol=1e-12, rtol=0
    )


def test_selection_rejects_unsupported_or_ambiguous_lookups():
    obj, _, _ = acquisition()
    for selector, error, match in [
        ({'b_value': 99}, ValueError, 'no matching'),
        ({'x': 1}, ValueError, 'explicit coordinate'),
        ({'b_value': [0]}, TypeError, 'scalar'),
        ({'b_matrix': 0}, NotImplementedError, 'one-dimensional'),
        ({'unknown': 0}, KeyError, 'unknown'),
    ]:
        with pytest.raises(error, match=match):
            obj.sel(selector)
    with pytest.raises(ValueError, match='duplicates'):
        obj.sel({}, duplicates='first')
    with pytest.raises(TypeError, match='mapping'):
        obj.isel([1])
    with pytest.raises(TypeError, match='mapping'):
        obj.sel([1])
    pyramid = Field('value', MultiscaleSource([np.arange(4), np.arange(2)]))
    obj = DataObject(
        'pyramid',
        StructuredGridDomain((CoordinateAxis('x', 4),)),
        {'value': pyramid},
    )
    with pytest.raises(NotImplementedError, match='single-level'):
        obj.isel({'x': slice(0, 2)})


def test_exact_lookup_does_not_round_integer_ticks_or_match_missing_sentinels():
    obj = DataObject(
        'events',
        StructuredGridDomain((CoordinateAxis('event', 2),)),
        {},
        coordinates={
            'time': Field(
                'time',
                ArraySource(np.array([2**60 + 1, -1], dtype=np.int64)),
                dimensions=('event',),
                missing_value=-1,
            )
        },
    )
    for value in (float(2**60), -1):
        with pytest.raises(ValueError, match='no matching'):
            obj.sel({'time': value})
    assert obj.sel({'time': 2**60 + 1}).domain.shape == (1,)


def test_selection_detects_changes_during_lookup_and_gather():
    class ChangingSource(ArraySource):
        def read(self, region, *, level=0):
            result = super().read(region, level=level)
            self.invalidate()
            return result

    source = ChangingSource(np.arange(4))
    obj = DataObject(
        'changing',
        StructuredGridDomain((CoordinateAxis('event', 4),)),
        {'value': Field('value', source)},
        coordinates={'time': Field('time', source, dimensions=('event',))},
    )
    with pytest.raises(SourceChangedError, match='during indexed read'):
        obj.isel({'event': [3, 0]}).fields['value'].read((slice(None),))
    with pytest.raises(SourceChangedError, match='resolving selection'):
        obj.sel({'time': 0})


def test_descending_axis_uses_one_bounded_forward_read():
    source = RecordingSource(np.arange(10001))
    obj = DataObject(
        'reverse',
        StructuredGridDomain((CoordinateAxis('x', 10001),)),
        {'value': Field('value', source)},
    )
    result = obj.isel({'x': slice(None, None, -2)})
    np.testing.assert_array_equal(
        result.fields['value'].read((slice(None),)), np.arange(10001)[::-2]
    )
    assert len(source.regions) == 1


def test_boolean_lookup_requires_boolean_coordinates():
    obj, _, _ = acquisition()
    with pytest.raises(TypeError, match='boolean coordinate'):
        obj.sel({'b_value': True})
    obj.update(
        coordinates={
            'valid': Field(
                'valid',
                ArraySource(np.array([False, True, False, False])),
                dimensions=('measurement',),
            )
        }
    )
    assert obj.sel({'valid': True}).domain.shape == (1, 6)
    with pytest.raises(TypeError, match='boolean coordinate'):
        obj.sel({'valid': 1})
