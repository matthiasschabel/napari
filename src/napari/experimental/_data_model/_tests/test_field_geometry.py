import numpy as np
import pytest

from napari.experimental._data_model import (
    ArraySource,
    CoordinateFrame,
    Field,
    FieldGeometry,
    Interpolation,
)


def test_declared_value_semantics_control_storage_and_default_policy():
    frame = CoordinateFrame('scanner', (('x', 'mm'), ('y', 'mm')))
    scalar = Field('signal', ArraySource(np.ones(3)))
    complex_value = Field(
        'signal',
        ArraySource(np.ones(3, dtype=complex)),
        geometry=FieldGeometry('complex'),
    )
    categorical = Field(
        'labels',
        ArraySource(np.ones(3, dtype=int)),
        geometry=FieldGeometry('categorical'),
    )
    vector = Field(
        'velocity',
        ArraySource(np.ones((3, 2))),
        unit='mm/s',
        component_axes=(1,),
        geometry=FieldGeometry(
            'vector', basis_frame=frame, basis='orthonormal'
        ),
    )
    tensor = Field(
        'diffusion',
        ArraySource(np.ones((3, 3))),
        unit='mm**2/s',
        component_axes=(1,),
        geometry=FieldGeometry(
            'symmetric_tensor',
            basis_frame=frame,
            packing=((0, 0), (1, 1), (0, 1)),
        ),
    )
    assert (
        scalar.interpolation
        is complex_value.interpolation
        is Interpolation.LINEAR
    )
    assert categorical.interpolation is Interpolation.NEAREST
    assert vector.interpolation is tensor.interpolation is None
    assert vector.geometry.components == ('x', 'y')
    assert tensor.geometry.basis is None
    bundle = FieldGeometry('components', components=('coil1', 'coil2'))
    assert bundle.basis_frame is None


@pytest.mark.parametrize(
    ('kwargs', 'error', 'match'),
    [
        ({'kind': 'unknown'}, ValueError, 'unknown'),
        (
            {'kind': 'vector', 'components': ('x',), 'basis_frame': 'world'},
            TypeError,
            'CoordinateFrame',
        ),
        (
            {'kind': 'vector', 'components': ('x',), 'basis': 'voxel'},
            ValueError,
            'unsupported',
        ),
        (
            {'kind': 'vector', 'components': ('x',), 'basis': 'orthonormal'},
            ValueError,
            'requires',
        ),
        ({'kind': 'vector', 'components': 'xyz'}, TypeError, 'sequence'),
        ({'kind': 'vector', 'components': ('x', 'x')}, ValueError, 'unique'),
        ({'kind': 'scalar', 'components': ('x',)}, ValueError, 'scalar'),
        ({'kind': 'vector'}, ValueError, 'require declared'),
        ({'kind': 'scalar', 'packing': ((0, 0),)}, ValueError, 'symmetric'),
        (
            {
                'kind': 'symmetric_tensor',
                'components': ('x',),
                'packing': ((0.0, 0),),
            },
            TypeError,
            'integer',
        ),
        (
            {
                'kind': 'symmetric_tensor',
                'components': ('x',),
                'packing': ((1, 1),),
            },
            ValueError,
            'cover',
        ),
    ],
)
def test_invalid_value_declarations(kwargs, error, match):
    with pytest.raises(error, match=match):
        FieldGeometry(**kwargs)


def test_value_storage_and_interpolation_mismatches():
    with pytest.raises(TypeError, match='geometry'):
        Field('value', ArraySource(np.ones(3)), geometry='vector')
    with pytest.raises(TypeError, match='complex storage'):
        Field(
            'value', ArraySource(np.ones(3)), geometry=FieldGeometry('complex')
        )
    with pytest.raises(ValueError, match='component shape'):
        Field(
            'value',
            ArraySource(np.ones(3)),
            geometry=FieldGeometry('vector', components=('x',)),
        )
    with pytest.raises(TypeError, match='real numeric'):
        Field(
            'value',
            ArraySource(np.ones((3, 1), dtype=complex)),
            component_axes=(1,),
            geometry=FieldGeometry('vector', components=('x',)),
        )
    with pytest.raises(ValueError, match='nearest'):
        Field(
            'value',
            ArraySource(np.ones(3)),
            geometry=FieldGeometry('categorical'),
            interpolation=Interpolation.LINEAR,
        )
    frame = CoordinateFrame('world', (('x', 'mm'),))
    with pytest.raises(ValueError, match='match the basis'):
        FieldGeometry('vector', components=('y',), basis_frame=frame)
