import numpy as np
import pytest

from napari.experimental._data_model import (
    ArraySource,
    CoordinateAxis,
    CoordinateSelection,
    DataObject,
    Field,
    StructuredGridDomain,
)


def test_acquisition_coordinates_share_measurements_without_voxel_broadcast():
    domain = StructuredGridDomain(
        (CoordinateAxis('measurement', 3), CoordinateAxis('x', 2))
    )
    signal = Field('signal', ArraySource(np.arange(6).reshape(3, 2)))
    b_value = Field(
        'b_value',
        ArraySource(np.array([0, 500, 500])),
        unit='s/mm**2',
        dimensions=('measurement',),
    )
    b_matrix = Field(
        'b_matrix',
        ArraySource(np.zeros((3, 3, 3))),
        component_axes=(1, 2),
        dimensions=('measurement',),
    )
    obj = DataObject(
        'diffusion',
        domain,
        {'signal': signal},
        coordinates={'b_value': b_value, 'b_matrix': b_matrix},
    )
    assert obj.coordinates['b_value'] is b_value
    assert b_matrix.shape == (3, 3, 3)
    np.testing.assert_array_equal(b_value.read((slice(1, 3),)), [500, 500])
    with pytest.raises(ValueError, match='does not match'):
        obj.update(
            domain=StructuredGridDomain(
                (CoordinateAxis('measurement', 4), CoordinateAxis('x', 2))
            ),
            fields={},
        )
    assert obj.revision == 0
    assert obj.replace(name='copy').coordinates['b_matrix'] is b_matrix
    observed = []
    obj.subscribe(
        lambda changed: observed.append(changed.coordinates['b_value'])
    )
    new_b_value = Field(
        'b_value',
        ArraySource(np.array([0, 1000, 1000])),
        unit='s/mm**2',
        dimensions=('measurement',),
    )
    obj.update(coordinates={**obj.coordinates, 'b_value': new_b_value})
    assert obj.revision == 1
    assert observed == [new_b_value]


def test_explicit_field_association_controls_selection_storage_order():
    domain = StructuredGridDomain(
        (CoordinateAxis('measurement', 3), CoordinateAxis('x', 2))
    )
    values = np.arange(6).reshape(2, 3)
    obj = DataObject(
        'observations',
        domain,
        {
            'transposed': Field(
                'transposed',
                ArraySource(values),
                dimensions=('x', 'measurement'),
            ),
            'weight': Field(
                'weight',
                ArraySource(np.array([1, 2, 3])),
                dimensions=('measurement',),
            ),
        },
    )
    selection = CoordinateSelection({'measurement': 1, 'x': 0})
    assert selection.read(obj, 'transposed') == 1
    assert selection.read(obj, 'weight') == 2


def test_coordinate_association_validation():
    domain = StructuredGridDomain((CoordinateAxis('event', 2),))
    with pytest.raises(TypeError, match='sequence of names'):
        Field('bad', ArraySource(np.ones(2)), dimensions='x')
    with pytest.raises(ValueError, match='unique'):
        Field(
            'bad', ArraySource(np.ones((2, 2))), dimensions=('event', 'event')
        )
    with pytest.raises(ValueError, match='non-component'):
        Field('bad', ArraySource(np.ones(2)), dimensions=())
    with pytest.raises(TypeError, match='string'):
        Field('bad', ArraySource(np.ones(2)), dimensions=(7,))
    with pytest.raises(ValueError, match='explicit dimensions'):
        DataObject(
            'bad',
            domain,
            coordinates={'time': Field('time', ArraySource(np.ones(2)))},
        )
    with pytest.raises(KeyError, match='unknown domain axis'):
        DataObject(
            'bad',
            domain,
            coordinates={
                'time': Field(
                    'time', ArraySource(np.ones(2)), dimensions=('missing',)
                )
            },
        )
    with pytest.raises(TypeError, match='mapping'):
        DataObject('bad', domain, coordinates=[])
    with pytest.raises(TypeError, match='Field instances'):
        DataObject('bad', domain, coordinates={'x': None})
    with pytest.raises(ValueError, match='mapping key'):
        DataObject(
            'bad',
            domain,
            coordinates={
                'wrong': Field(
                    'time', ArraySource(np.ones(2)), dimensions=('event',)
                )
            },
        )


def test_scalar_and_multidimensional_coordinates_preserve_integer_precision():
    domain = StructuredGridDomain(
        (CoordinateAxis('readout', 2), CoordinateAxis('sample', 3))
    )
    ticks = np.arange(6, dtype=np.uint64).reshape(2, 3) + np.uint64(2**60)
    obj = DataObject(
        'acquisition',
        domain,
        coordinates={
            'ticks': Field(
                'ticks', ArraySource(ticks), dimensions=('readout', 'sample')
            ),
            'tick_period': Field(
                'tick_period',
                ArraySource(np.array(5)),
                unit='ns',
                dimensions=(),
            ),
        },
    )
    np.testing.assert_array_equal(
        obj.coordinates['ticks'].read((slice(1, 2), slice(1, 3))),
        ticks[1:2, 1:3],
    )
    assert obj.coordinates['ticks'].dtype == np.dtype('uint64')
    assert obj.coordinates['tick_period'].read(()) == 5
