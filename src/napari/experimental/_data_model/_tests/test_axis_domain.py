import numpy as np
import pytest

from napari.experimental._data_model import (
    AxisRole,
    CoordinateAxis,
    CoordinateFrame,
    FrameAxis,
    StructuredGridDomain,
)


def test_dataclass_replacement_preserves_auxiliary_coordinates() -> None:
    import copy
    import pickle
    from dataclasses import replace

    axis = CoordinateAxis(
        'measurement', 2, aux_values={'b': np.array([0, 500])}
    )
    renamed = replace(axis, name='acquisition')
    for restored in (
        renamed,
        copy.copy(renamed),
        copy.deepcopy(renamed),
        pickle.loads(pickle.dumps(renamed)),
    ):
        np.testing.assert_array_equal(restored.aux_values['b'], [0, 500])
        with pytest.raises(TypeError):
            restored.aux_values['other'] = np.ones(2)
        with pytest.raises(ValueError, match='read-only'):
            restored.aux_values['b'][0] = 1


def test_irregular_axis_with_auxiliary_coordinates() -> None:
    echo_times = np.array([1.2, 3.7, 8.1, 15.0])
    gradient_directions = np.eye(4, 3)
    axis = CoordinateAxis(
        'echo_time',
        4,
        unit='ms',
        values=echo_times,
        aux_values={
            'b_value': np.array([0.0, 500.0, 1000.0, 1500.0]),
            'gradient_direction': gradient_directions,
        },
    )

    assert axis.values is not echo_times
    assert axis.aux_values['gradient_direction'] is not gradient_directions
    np.testing.assert_array_equal(axis.values, echo_times)
    np.testing.assert_array_equal(
        axis.aux_values['gradient_direction'], gradient_directions
    )
    assert not axis.values.flags.writeable
    assert not axis.aux_values['gradient_direction'].flags.writeable


def test_axis_values_are_unchanged_when_inputs_are_mutated() -> None:
    values = np.array([1.0, 2.0])
    auxiliary = np.array([3.0, 4.0])
    axis = CoordinateAxis(
        'x', 2, values=values, aux_values={'auxiliary': auxiliary}
    )

    values[0] = 10.0
    auxiliary[0] = 20.0

    np.testing.assert_array_equal(axis.values, [1.0, 2.0])
    np.testing.assert_array_equal(axis.aux_values['auxiliary'], [3.0, 4.0])


def test_axis_auxiliary_mapping_is_immutable() -> None:
    axis = CoordinateAxis(
        'x', 2, aux_values={'position': np.array([0.0, 1.0])}
    )

    with pytest.raises(TypeError):
        axis.aux_values['other'] = np.ones(2)  # type: ignore[index]


def test_categorical_axis_labels() -> None:
    axis = CoordinateAxis(
        'channel',
        3,
        role=AxisRole.LOCAL,
        dtype_kind='categorical',
        categorical_labels=('red', 'green', 'blue'),
    )

    assert axis.categorical_labels == ('red', 'green', 'blue')


@pytest.mark.parametrize('name', ['', '   '])
def test_axis_rejects_empty_name(name: str) -> None:
    with pytest.raises(ValueError, match='axis name must not be empty'):
        CoordinateAxis(name, 2)


def test_axis_rejects_non_string_name() -> None:
    with pytest.raises(TypeError, match='axis name must be a string'):
        CoordinateAxis(12, 2)  # type: ignore[arg-type]


@pytest.mark.parametrize('size', [-1, -10])
def test_axis_rejects_negative_size(size: int) -> None:
    with pytest.raises(ValueError, match='non-negative'):
        CoordinateAxis('x', size)


@pytest.mark.parametrize('size', [True, 2.5])
def test_axis_rejects_non_integer_size(size: object) -> None:
    with pytest.raises(TypeError, match='size must be an integer'):
        CoordinateAxis('x', size)  # type: ignore[arg-type]


def test_axis_rejects_invalid_role() -> None:
    with pytest.raises(TypeError, match='role must be an AxisRole'):
        CoordinateAxis('x', 2, role='coordinate')  # type: ignore[arg-type]


def test_axis_rejects_invalid_unit() -> None:
    with pytest.raises(ValueError, match='not pint-compatible'):
        CoordinateAxis('x', 2, unit='not_a_real_unit')


def test_axis_rejects_non_string_unit() -> None:
    with pytest.raises(TypeError, match='unit must be a string or None'):
        CoordinateAxis('x', 2, unit=2)  # type: ignore[arg-type]


def test_axis_rejects_invalid_dtype_kind() -> None:
    with pytest.raises(ValueError, match='unsupported axis dtype'):
        CoordinateAxis('x', 2, dtype_kind='complex')  # type: ignore[arg-type]


def test_axis_rejects_non_array_values() -> None:
    with pytest.raises(TypeError, match='values must be a numpy array'):
        CoordinateAxis('x', 2, values=[0.0, 1.0])  # type: ignore[arg-type]


def test_axis_rejects_non_vector_values() -> None:
    with pytest.raises(ValueError, match='one-dimensional'):
        CoordinateAxis('x', 2, values=np.ones((2, 1)))


def test_axis_rejects_inconsistent_value_length() -> None:
    with pytest.raises(ValueError, match='length must match'):
        CoordinateAxis('x', 2, values=np.array([0.0]))


@pytest.mark.parametrize(
    ('dtype_kind', 'values'),
    [
        ('float', np.array([0, 1])),
        ('int', np.array([0.0, 1.0])),
        ('datetime', np.array([0, 1])),
    ],
)
def test_axis_rejects_dtype_kind_mismatch(
    dtype_kind: str, values: np.ndarray
) -> None:
    with pytest.raises(TypeError, match='dtype does not match'):
        CoordinateAxis(
            'x',
            2,
            values=values,
            dtype_kind=dtype_kind,  # type: ignore[arg-type]
        )


def test_axis_rejects_labels_on_non_categorical_axis() -> None:
    with pytest.raises(ValueError, match='require dtype_kind'):
        CoordinateAxis('x', 2, categorical_labels=('left', 'right'))


def test_axis_rejects_inconsistent_label_count() -> None:
    with pytest.raises(ValueError, match='label count must match'):
        CoordinateAxis(
            'channel',
            2,
            dtype_kind='categorical',
            categorical_labels=('red',),
        )


def test_axis_rejects_non_string_labels() -> None:
    with pytest.raises(TypeError, match='labels must be strings'):
        CoordinateAxis(
            'channel',
            2,
            dtype_kind='categorical',
            categorical_labels=('red', 2),  # type: ignore[arg-type]
        )


def test_axis_rejects_non_mapping_auxiliary_values() -> None:
    with pytest.raises(TypeError, match='must be a mapping'):
        CoordinateAxis('x', 2, aux_values=())  # type: ignore[arg-type]


def test_axis_rejects_non_array_auxiliary_values() -> None:
    with pytest.raises(TypeError, match='must be arrays'):
        CoordinateAxis(
            'x',
            2,
            aux_values={'position': [0.0, 1.0]},  # type: ignore[dict-item]
        )


def test_axis_rejects_scalar_auxiliary_values() -> None:
    with pytest.raises(ValueError, match='at least one dimension'):
        CoordinateAxis('x', 2, aux_values={'position': np.array(1.0)})


def test_axis_rejects_inconsistent_auxiliary_length() -> None:
    with pytest.raises(ValueError, match='length must match'):
        CoordinateAxis('x', 2, aux_values={'position': np.ones(3)})


def test_domain_shape_lookup_and_intrinsic_frame() -> None:
    axes = (
        CoordinateAxis('time', 4, unit='s'),
        CoordinateAxis('z', 8, role=AxisRole.LOCAL, unit='mm'),
    )
    domain = StructuredGridDomain(axes)

    assert domain.shape == (4, 8)
    assert domain.axis('z') is axes[1]
    assert domain.axis_index('z') == 1
    assert domain.intrinsic_frame is domain.intrinsic_frame
    assert domain.intrinsic_frame.name == 'intrinsic'
    assert domain.intrinsic_frame.axes == (
        FrameAxis('time', 's'),
        FrameAxis('z', 'mm'),
    )


def test_domain_rejects_duplicate_axis_names() -> None:
    with pytest.raises(ValueError, match='axis names must be unique'):
        StructuredGridDomain((CoordinateAxis('x', 2), CoordinateAxis('x', 3)))


def test_domain_rejects_component_axis_as_domain_axis() -> None:
    with pytest.raises(ValueError, match='cannot also be a domain axis'):
        StructuredGridDomain(
            (CoordinateAxis('rgb', 3, role=AxisRole.COMPONENT),)
        )


def test_domain_rejects_non_axis_descriptor() -> None:
    with pytest.raises(TypeError, match='CoordinateAxis instances'):
        StructuredGridDomain(('x',))  # type: ignore[arg-type]


def test_domain_axis_lookup_rejects_unknown_name() -> None:
    domain = StructuredGridDomain((CoordinateAxis('x', 2),))

    with pytest.raises(KeyError, match='unknown domain axis'):
        domain.axis('y')
    with pytest.raises(KeyError, match='unknown domain axis'):
        domain.axis_index('y')


def test_coordinate_frames_compare_by_identity() -> None:
    first = CoordinateFrame('scanner', (('z', 'mm'), ('y', 'mm')))
    second = CoordinateFrame('scanner', (('z', 'mm'), ('y', 'mm')))

    assert first == first
    assert first != second


def test_axes_compare_and_hash_by_identity() -> None:
    first = CoordinateAxis('x', 2, values=np.array([0.0, 1.0]))
    second = CoordinateAxis('x', 2, values=np.array([0.0, 1.0]))

    mapping = {first: 'first', second: 'second'}

    assert first == first
    assert first != second
    assert mapping == {first: 'first', second: 'second'}


def test_domains_compare_and_hash_by_identity() -> None:
    first = StructuredGridDomain((CoordinateAxis('x', 2),))
    second = StructuredGridDomain((CoordinateAxis('x', 2),))

    assert first != second
    assert {first, second} == {first, second}


def test_frame_rejects_duplicate_axis_names() -> None:
    with pytest.raises(ValueError, match='frame axis names must be unique'):
        CoordinateFrame('scanner', (('x', 'mm'), ('x', 'mm')))
