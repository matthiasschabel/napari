import numpy as np
import pytest

from napari.experimental._data_model import (
    AffineMapping,
    ArraySource,
    AxisRole,
    CoordinateAxis,
    CoordinateEmbedding,
    CoordinateFrame,
    CoordinateSelection,
    DataObject,
    Field,
    MultiscaleSource,
    StructuredGridDomain,
)


def make_data_object() -> DataObject:
    domain = StructuredGridDomain(
        (
            CoordinateAxis(
                'echo_time',
                4,
                unit='ms',
                values=np.array([1.2, 3.7, 8.1, 15.0]),
            ),
            CoordinateAxis('z', 2, unit='mm'),
            CoordinateAxis('y', 3, unit='mm'),
        )
    )
    signal = Field('signal', ArraySource(np.arange(24).reshape(4, 2, 3)))
    return DataObject('image', domain, {'signal': signal})


def test_affine_round_trip_with_frame_identity() -> None:
    source = CoordinateFrame('image', (('x', 'mm'), ('y', 'mm')))
    target = CoordinateFrame('scanner', (('x', 'mm'), ('y', 'mm')))
    mapping = AffineMapping(
        source,
        target,
        [[2.0, 0.5], [-0.25, 3.0]],
        [10.0, -4.0],
    )
    points = np.array([[0.0, 0.0], [1.5, -2.0], [7.0, 3.25]])

    mapped = mapping.map_points(points, frame=source)
    restored = mapping.inverse().map_points(mapped, frame=target)

    np.testing.assert_allclose(restored, points, rtol=1e-12, atol=1e-12)


def test_mapping_rejects_points_from_equivalent_but_distinct_frame() -> None:
    source = CoordinateFrame('image', (('x', 'mm'),))
    equivalent = CoordinateFrame('image', (('x', 'mm'),))
    target = CoordinateFrame('scanner', (('x', 'mm'),))
    mapping = AffineMapping(source, target, [[1.0]])

    with pytest.raises(ValueError, match='not expressed in the source frame'):
        mapping.map_points([[1.0]], frame=equivalent)


def test_mapping_rejects_point_dimension_mismatch() -> None:
    source = CoordinateFrame('image', (('x', 'mm'), ('y', 'mm')))
    target = CoordinateFrame('scanner', (('x', 'mm'), ('y', 'mm')))
    mapping = AffineMapping(source, target, np.eye(2))

    with pytest.raises(ValueError, match='source frame dimension'):
        mapping.map_points([1.0], frame=source)


def test_mapping_rejects_matrix_shape_mismatch() -> None:
    source = CoordinateFrame('image', (('x', 'mm'),))
    target = CoordinateFrame('scanner', (('x', 'mm'), ('y', 'mm')))

    with pytest.raises(ValueError, match='matrix shape must be'):
        AffineMapping(source, target, np.eye(2))


@pytest.mark.parametrize(
    ('source', 'target'),
    [
        (object(), CoordinateFrame('target', (('x', None),))),
        (CoordinateFrame('source', (('x', None),)), object()),
    ],
)
def test_mapping_rejects_invalid_frames(
    source: object, target: object
) -> None:
    with pytest.raises(TypeError, match='must be CoordinateFrame objects'):
        AffineMapping(source, target, [[1.0]])  # type: ignore[arg-type]


def test_mapping_rejects_offset_shape_mismatch() -> None:
    source = CoordinateFrame('image', (('x', 'mm'),))
    target = CoordinateFrame('scanner', (('x', 'mm'),))

    with pytest.raises(ValueError, match='offset shape'):
        AffineMapping(source, target, [[1.0]], [0.0, 1.0])


def test_mapping_rejects_singular_inverse() -> None:
    source = CoordinateFrame('image', (('x', None), ('y', None)))
    target = CoordinateFrame('scanner', (('x', None), ('y', None)))
    mapping = AffineMapping(source, target, [[1.0, 2.0], [2.0, 4.0]])

    with pytest.raises(NotImplementedError, match='singular'):
        mapping.inverse()


def test_mapping_rejects_non_square_inverse() -> None:
    source = CoordinateFrame('line', (('x', None),))
    target = CoordinateFrame('plane', (('x', None), ('y', None)))
    mapping = AffineMapping(source, target, [[1.0], [2.0]])

    with pytest.raises(NotImplementedError, match='square'):
        mapping.inverse()


def test_mapping_rejects_near_singular_inverse() -> None:
    source = CoordinateFrame('image', (('x', None), ('y', None)))
    target = CoordinateFrame('scanner', (('x', None), ('y', None)))
    mapping = AffineMapping(source, target, [[1.0, 0.0], [0.0, 1e-13]])

    with pytest.raises(
        NotImplementedError, match='near-singular: condition number'
    ):
        mapping.inverse()


def test_coordinate_embedding_uses_domain_intrinsic_frame() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('z', 2, unit='mm'), CoordinateAxis('y', 3, unit='mm'))
    )
    scanner = CoordinateFrame(
        'scanner', (('superior', 'mm'), ('anterior', 'mm'))
    )
    embedding = CoordinateEmbedding(domain, scanner, np.eye(2), [2.0, 3.0])

    assert embedding.domain is domain
    assert embedding.source_frame is domain.intrinsic_frame
    np.testing.assert_allclose(
        embedding.map_points([[0.0, 0.0]], frame=domain.intrinsic_frame),
        [[2.0, 3.0]],
        rtol=0.0,
        atol=1e-12,
    )


def test_coordinate_embedding_rejects_invalid_domain() -> None:
    target = CoordinateFrame('world', (('x', None),))

    with pytest.raises(TypeError, match='Domain protocol'):
        CoordinateEmbedding(object(), target, [[1.0]])  # type: ignore[arg-type]


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
            {'shape': (2,), 'intrinsic_frame': object()},
        )(),
    ],
)
def test_coordinate_embedding_validates_domain_attribute_types(
    domain: object,
) -> None:
    target = CoordinateFrame('world', (('x', None),))

    with pytest.raises(
        TypeError, match=r'embedding domain\.(shape|intrinsic_frame)'
    ):
        CoordinateEmbedding(  # type: ignore[arg-type]
            domain, target, [[1.0]]
        )


def test_selection_resolves_exact_irregular_coordinate_value() -> None:
    data_object = make_data_object()
    selection = CoordinateSelection({'echo_time': 8.1})

    assert selection.region_for(data_object, 'signal') == (
        slice(2, 3),
        slice(None),
        slice(None),
    )
    np.testing.assert_array_equal(
        selection.read(data_object, 'signal'),
        np.arange(24).reshape(4, 2, 3)[2],
    )


def test_selection_resolves_nearest_irregular_coordinate_value() -> None:
    data_object = make_data_object()
    selection = CoordinateSelection({'echo_time': 7.0})

    assert selection.region_for(data_object, 'signal')[0] == slice(2, 3)


def test_integer_selector_is_positional_and_float_selector_is_a_value() -> (
    None
):
    domain = StructuredGridDomain(
        (CoordinateAxis('x', 3, values=np.array([10.0, 20.0, 30.0])),)
    )
    data_object = DataObject(
        'line', domain, {'signal': Field('signal', ArraySource(np.arange(3)))}
    )

    integer_result = CoordinateSelection({'x': 1}).read(data_object, 'signal')
    float_result = CoordinateSelection({'x': 1.0}).read(data_object, 'signal')

    np.testing.assert_array_equal(integer_result, np.array(1))
    np.testing.assert_array_equal(float_result, np.array(0))


def test_nearest_selector_clamps_far_outside_axis_values() -> None:
    data_object = make_data_object()

    region = CoordinateSelection(
        {'echo_time': 1_000.0}, method='nearest'
    ).region_for(data_object, 'signal')

    assert region[0] == slice(3, 4)


def test_exact_selector_rejects_missing_value() -> None:
    with pytest.raises(ValueError, match='is not present'):
        CoordinateSelection({'echo_time': 1_000.0}, method='exact').apply(
            make_data_object()
        )


def test_selection_resolves_nearest_datetime_value() -> None:
    dates = np.array(
        ['2026-01-01', '2026-01-05', '2026-02-01'], dtype='datetime64[D]'
    )
    domain = StructuredGridDomain(
        (CoordinateAxis('date', 3, values=dates, dtype_kind='datetime'),)
    )
    data_object = DataObject(
        'series',
        domain,
        {'signal': Field('signal', ArraySource(np.arange(3)))},
    )

    result = CoordinateSelection({'date': np.datetime64('2026-01-08')}).read(
        data_object, 'signal'
    )

    np.testing.assert_array_equal(result, np.array(1))


def test_selection_resolves_categorical_label() -> None:
    domain = StructuredGridDomain(
        (
            CoordinateAxis(
                'channel',
                3,
                role=AxisRole.LOCAL,
                dtype_kind='categorical',
                categorical_labels=('red', 'green', 'blue'),
            ),
        )
    )
    image = DataObject(
        'image',
        domain,
        {'signal': Field('signal', ArraySource(np.arange(3)))},
    )

    result = CoordinateSelection({'channel': 'green'}).read(image, 'signal')

    np.testing.assert_array_equal(result, np.array(1))


def test_selection_preserves_component_axis() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    rgb_values = np.arange(18).reshape(2, 3, 3)
    image = DataObject(
        'rgb',
        domain,
        {'rgb': Field('rgb', ArraySource(rgb_values), component_axes=(2,))},
    )

    result = CoordinateSelection({'y': 1}).read(image, 'rgb')

    assert result.shape == (3, 3)
    np.testing.assert_array_equal(result, rgb_values[1])


def test_selection_apply_returns_regions_for_each_field_layout() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 2), CoordinateAxis('x', 3))
    )
    scalar = Field('scalar', ArraySource(np.zeros((2, 3))))
    vector = Field(
        'vector',
        ArraySource(np.zeros((4, 2, 3))),
        component_axes=(0,),
    )
    data_object = DataObject(
        'sample', domain, {'scalar': scalar, 'vector': vector}
    )

    regions = CoordinateSelection({'x': 1}).apply(data_object)

    assert regions == {
        'scalar': (slice(None), slice(1, 2)),
        'vector': (slice(None), slice(None), slice(1, 2)),
    }


def test_selection_rejects_unknown_axis() -> None:
    with pytest.raises(KeyError, match='unknown selection axis'):
        CoordinateSelection({'coil': 0}).apply(make_data_object())


def test_selection_rejects_unknown_field() -> None:
    with pytest.raises(KeyError, match='unknown field'):
        CoordinateSelection({'echo_time': 0}).region_for(
            make_data_object(), 'magnitude'
        )


def test_selection_read_rejects_unknown_field() -> None:
    with pytest.raises(KeyError, match='unknown field'):
        CoordinateSelection({'echo_time': 0}).read(
            make_data_object(), 'magnitude'
        )


def test_selection_rejects_out_of_range_index() -> None:
    with pytest.raises(IndexError, match='outside axis'):
        CoordinateSelection({'echo_time': 4}).apply(make_data_object())


def test_selection_rejects_negative_positional_index() -> None:
    with pytest.raises(IndexError, match='outside axis'):
        CoordinateSelection({'echo_time': -1}).apply(make_data_object())


def test_selection_rejects_value_without_explicit_coordinates() -> None:
    with pytest.raises(ValueError, match='no explicit values'):
        CoordinateSelection({'z': 0.5}).apply(make_data_object())


def test_selection_rejects_unknown_categorical_value() -> None:
    domain = StructuredGridDomain(
        (
            CoordinateAxis(
                'channel',
                2,
                role=AxisRole.LOCAL,
                dtype_kind='categorical',
                values=np.array(['red', 'green']),
            ),
        )
    )
    image = DataObject(
        'image',
        domain,
        {'signal': Field('signal', ArraySource(np.arange(2)))},
    )

    with pytest.raises(ValueError, match='is not present'):
        CoordinateSelection({'channel': 'blue'}).apply(image)


def test_selection_rejects_non_mapping() -> None:
    with pytest.raises(TypeError, match='must be a mapping'):
        CoordinateSelection([])  # type: ignore[arg-type]


@pytest.mark.parametrize('name', ['', 1])
def test_selection_rejects_invalid_axis_name(name: object) -> None:
    with pytest.raises(ValueError, match='non-empty strings'):
        CoordinateSelection({name: 0})  # type: ignore[dict-item]


def test_selection_rejects_invalid_method() -> None:
    with pytest.raises(ValueError, match='method'):
        CoordinateSelection({}, method='linear')  # type: ignore[arg-type]


def test_selection_rejects_non_data_object() -> None:
    with pytest.raises(TypeError, match='requires a DataObject'):
        CoordinateSelection({}).apply(object())  # type: ignore[arg-type]


def test_selection_rejects_unresolvable_value_type() -> None:
    with pytest.raises(ValueError, match='cannot be resolved'):
        CoordinateSelection({'echo_time': object()}).apply(make_data_object())


def test_selection_passes_level_to_field_source() -> None:
    source = MultiscaleSource((np.arange(2), np.arange(2) + 10))
    data_object = DataObject(
        'line',
        StructuredGridDomain((CoordinateAxis('x', 2),)),
        {'signal': Field('signal', source)},
    )

    result = CoordinateSelection({'x': 1}).read(data_object, 'signal', level=1)

    np.testing.assert_array_equal(result, np.array(11))


def test_selection_rejects_decimated_level_without_index_policy() -> None:
    source = MultiscaleSource((np.arange(4), np.arange(2)))
    data_object = DataObject(
        'line',
        StructuredGridDomain((CoordinateAxis('x', 4),)),
        {'signal': Field('signal', source)},
    )

    with pytest.raises(ValueError, match='per-level index policy'):
        CoordinateSelection({'x': 1}).read(data_object, 'signal', level=1)
