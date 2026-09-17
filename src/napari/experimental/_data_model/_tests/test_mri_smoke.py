import numpy as np

from napari.experimental._data_model import (
    ArraySource,
    CoordinateAxis,
    CoordinateEmbedding,
    CoordinateFrame,
    CoordinateSelection,
    DataObject,
    Field,
    StructuredGridDomain,
)


def test_mri_coordinate_selection_reads_spatial_block() -> None:
    axes = (
        CoordinateAxis('time', 2, unit='s', values=np.array([0.0, 2.5])),
        CoordinateAxis(
            'echo_time',
            4,
            unit='ms',
            values=np.array([1.2, 3.7, 8.1, 15.0]),
        ),
        CoordinateAxis(
            'flip_angle',
            3,
            unit='deg',
            values=np.array([5.0, 15.0, 30.0]),
        ),
        CoordinateAxis('z', 2, unit='mm'),
        CoordinateAxis('y', 3, unit='mm'),
        CoordinateAxis('x', 4, unit='mm'),
    )
    domain = StructuredGridDomain(axes)
    shape = domain.shape
    signal_values = (
        np.arange(np.prod(shape)).reshape(shape).astype(np.complex64)
    )
    signal_values *= 1 + 0.5j
    signal = Field('signal', ArraySource(signal_values), unit='a.u.')

    scanner = CoordinateFrame(
        'scanner',
        (
            ('time', 's'),
            ('echo_time', 'ms'),
            ('flip_angle', 'deg'),
            ('superior', 'mm'),
            ('anterior', 'mm'),
            ('left', 'mm'),
        ),
    )
    matrix = np.diag([1.0, 1.0, 1.0, 2.0, 1.5, 1.5])
    embedding = CoordinateEmbedding(
        domain,
        scanner,
        matrix,
        [0.0, 0.0, 0.0, -10.0, 20.0, 30.0],
    )
    image = DataObject(
        'multi-echo acquisition',
        domain,
        {'signal': signal},
        embeddings=[embedding],
        metadata={'modality': 'MRI'},
    )

    selection = CoordinateSelection(
        {'time': 2.5, 'echo_time': 7.0, 'flip_angle': 1}
    )
    block = selection.read(image, 'signal')

    assert block.shape == (2, 3, 4)
    assert np.issubdtype(block.dtype, np.complexfloating)
    np.testing.assert_array_equal(block, signal_values[1, 2, 1])
    np.testing.assert_allclose(
        embedding.map_points(
            [[0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
            frame=domain.intrinsic_frame,
        ),
        [[0.0, 0.0, 0.0, -10.0, 20.0, 30.0]],
        rtol=0.0,
        atol=1e-12,
    )
