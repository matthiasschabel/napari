import numpy as np
import pytest

from napari.experimental._data_model import (
    ArraySource,
    CoordinateFrame,
    DataObject,
    Field,
    FieldGeometry,
    MeshDomain,
)


def test_mesh_point_velocity_and_cell_labels_have_independent_associations():
    frame = CoordinateFrame('specimen', (('x', 'mm'), ('y', 'mm')))
    mesh = MeshDomain(
        ArraySource(
            np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        ),
        ArraySource(np.array([[0, 1, 2], [1, 2, 3]])),
        frame,
    )
    obj = DataObject(
        'mesh',
        mesh,
        {
            'velocity': Field(
                'velocity',
                ArraySource(np.arange(8).reshape(4, 2)),
                dimensions=('point',),
                component_axes=(1,),
                unit='mm/s',
                geometry=FieldGeometry('vector', basis_frame=frame),
            ),
            'label': Field(
                'label',
                ArraySource(np.array([10, 20])),
                dimensions=('cell',),
                geometry=FieldGeometry('categorical'),
            ),
            'constant': Field(
                'constant', ArraySource(np.array(4)), dimensions=()
            ),
        },
    )
    np.testing.assert_array_equal(
        obj.fields['velocity'].read((slice(2, 4), slice(None))),
        [[4, 5], [6, 7]],
    )
    assert mesh.association_shape(('point',)) == (4,)
    assert mesh.association_shape(('cell',)) == (2,)
    with pytest.raises(ValueError, match='does not match'):
        obj.update(
            fields={
                'wrong': Field(
                    'wrong', ArraySource(np.arange(2)), dimensions=('point',)
                )
            }
        )
    with pytest.raises(ValueError, match='mesh dimensions'):
        mesh.association_shape(('point', 'cell'))
    with pytest.raises(TypeError, match='StructuredGridDomain'):
        obj.isel({'point': 0})
    with pytest.raises(TypeError, match='StructuredGridDomain'):
        obj.sel({'point': 0})


@pytest.mark.parametrize(
    ('points', 'cells', 'frame', 'error', 'match'),
    [
        (
            np.ones((2, 2)),
            np.ones((1, 2), dtype=int),
            'world',
            TypeError,
            'CoordinateFrame',
        ),
        (
            np.ones((2, 3)),
            np.ones((1, 2), dtype=int),
            None,
            ValueError,
            'point shape',
        ),
        (
            np.ones((2, 2), dtype=complex),
            np.ones((1, 2), dtype=int),
            None,
            TypeError,
            'real numeric',
        ),
        (
            np.ones((2, 2)),
            np.ones((1, 1), dtype=int),
            None,
            ValueError,
            'at least two',
        ),
        (np.ones((2, 2)), np.ones((1, 2)), None, TypeError, 'integer'),
    ],
)
def test_mesh_header_validation(points, cells, frame, error, match):
    if frame is None:
        frame = CoordinateFrame('world', (('x', 'mm'), ('y', 'mm')))
    with pytest.raises(error, match=match):
        MeshDomain(ArraySource(points), ArraySource(cells), frame)


def test_foreign_mesh_sources_can_expose_numpy_integer_shapes():
    class NumpyShapeSource(ArraySource):
        @property
        def shape(self):
            return tuple(np.int64(size) for size in super().shape)

    frame = CoordinateFrame('xy', (('x', 'mm'), ('y', 'mm')))
    mesh = MeshDomain(
        NumpyShapeSource(np.ones((3, 2))),
        NumpyShapeSource(np.array([[0, 1, 2]])),
        frame,
    )
    DataObject(
        'mesh',
        mesh,
        {
            'value': Field(
                'value', ArraySource(np.arange(3)), dimensions=('point',)
            )
        },
    )


def test_foreign_association_results_are_validated_at_object_boundary():
    class ForeignDomain:
        shape = (2,)
        intrinsic_frame = CoordinateFrame('index', (('x', None),))

        def association_shape(self, dimensions):
            return (True,)

    with pytest.raises(TypeError, match='nonnegative integer'):
        DataObject(
            'foreign',
            ForeignDomain(),
            {
                'value': Field(
                    'value', ArraySource(np.ones(2)), dimensions=('element',)
                )
            },
        )
