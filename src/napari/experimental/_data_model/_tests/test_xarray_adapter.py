from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import dask.array as da
import numpy as np
import pytest
import xarray as xr

from napari.experimental._data_model import (
    ArraySource,
    AxisRole,
    CoordinateFrame,
    CoordinateGraph,
    FrameRegistry,
    Unplaced,
    XarrayEmbedding,
    data_object_from_xarray,
    embedding_from_reference_frame,
)

_GEOMETRY_ATOL = 1e-12


class CountingArray:
    def __init__(self, values: np.ndarray) -> None:
        self.values = values
        self.shape = values.shape
        self.dtype = values.dtype
        self.ndim = values.ndim
        self.regions: list[Any] = []

    def __getitem__(self, region: Any) -> np.ndarray:
        self.regions.append(region)
        return self.values[region]


def _reference_frame(**changes: Any) -> SimpleNamespace:
    values = {
        'origin': (10.0, 20.0, 30.0),
        'basis_vectors': np.eye(3),
        'in_plane_spacing': (0.8, 0.6),
        'slice_positions': (0.0, 2.0, 4.0),
        'units': 'mm',
        'convention': 'LPS',
        'frame_of_reference_uid': '1.2.840.123',
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_xarray_adapter_preserves_labeled_axes_and_complex_field() -> None:
    echo_times = np.array([4.92, 9.84, 19.68, 29.52])
    components = np.array(['MAGNITUDE', 'PHASE'])
    dates = np.array(['2026-08-01', '2026-08-03'], dtype='datetime64[D]')
    values = np.zeros((4, 2, 2, 3), dtype=np.complex64)
    array = xr.DataArray(
        values,
        dims=('EchoTime', 'ComplexImageComponent', 'date', 'sample'),
        coords={
            'EchoTime': echo_times,
            'ComplexImageComponent': components,
            'date': dates,
        },
        name='signal',
    )
    array.coords['EchoTime'].attrs['units'] = 'ms'

    data_object = data_object_from_xarray(
        array,
        name='multi-echo GRE',
        units={'EchoTime': 's', 'sample': 'mm'},
        axis_roles={'sample': AxisRole.LOCAL},
    )

    assert data_object.name == 'multi-echo GRE'
    assert tuple(axis.name for axis in data_object.domain.axes) == array.dims
    echo_axis, component_axis, date_axis, sample_axis = data_object.domain.axes
    np.testing.assert_array_equal(echo_axis.values, echo_times)
    assert echo_axis.unit == 'ms'
    assert echo_axis.dtype_kind == 'float'
    np.testing.assert_array_equal(component_axis.values, components)
    assert component_axis.dtype_kind == 'categorical'
    assert component_axis.categorical_labels == ('MAGNITUDE', 'PHASE')
    assert component_axis.role is AxisRole.COORDINATE
    np.testing.assert_array_equal(date_axis.values, dates)
    assert date_axis.dtype_kind == 'datetime'
    assert sample_axis.values is None
    assert sample_axis.dtype_kind == 'int'
    assert sample_axis.unit == 'mm'
    assert sample_axis.role is AxisRole.LOCAL

    field = data_object.fields['signal']
    assert isinstance(field.source, ArraySource)
    assert field.source.array is values
    assert field.dtype == np.dtype(np.complex64)
    assert field.component_axes == ()


def test_xarray_adapter_default_names() -> None:
    named = data_object_from_xarray(
        xr.DataArray(np.ones(2), dims=('x',), name='intensity')
    )
    unnamed = data_object_from_xarray(xr.DataArray(np.ones(2), dims=('x',)))

    assert named.name == 'intensity'
    assert tuple(named.fields) == ('intensity',)
    assert unnamed.name == 'xarray'
    assert tuple(unnamed.fields) == ('data',)


def test_xarray_adapter_preserves_lazy_auxiliary_coordinates():
    from dask.callbacks import Callback

    ticks = np.arange(6, dtype=np.uint64).reshape(3, 2) + np.uint64(2**60)
    array = xr.DataArray(
        da.zeros((3, 2), chunks=(1, 2)),
        dims=('measurement', 'sample'),
        coords={
            'b_value': ('measurement', da.from_array([0, 500, 500], chunks=1)),
            'ticks': (
                ('measurement', 'sample'),
                da.from_array(ticks, chunks=(1, 2)),
            ),
            'tick_period': 5,
        },
    )
    array.coords['b_value'].attrs.update(units='s/mm**2', _FillValue=-1)
    array.coords['tick_period'].attrs['units'] = 'ns'
    computed = []
    with Callback(posttask=lambda *args: computed.append(args[0])):
        obj = data_object_from_xarray(array)
    assert computed == []
    assert obj.coordinates['b_value'].dimensions == ('measurement',)
    assert obj.coordinates['b_value'].unit == 's/mm**2'
    assert obj.coordinates['b_value'].missing_value == -1
    assert obj.coordinates['tick_period'].dimensions == ()
    np.testing.assert_array_equal(
        obj.coordinates['b_value'].read((slice(1, 3),)), [500, 500]
    )
    np.testing.assert_array_equal(
        obj.coordinates['ticks'].read((slice(1, 2), slice(None))), ticks[1:2]
    )


def test_auxiliary_coordinate_units_are_retained_when_not_interpretable():
    array = xr.DataArray(
        np.arange(3),
        dims=('sample',),
        coords={'acquisition': ('sample', np.arange(3))},
    )
    for unit in ('degrees_north', 'DN', 'seconds since 1970-01-01'):
        array.coords['acquisition'].attrs['units'] = unit
        with pytest.warns(UserWarning, match=r'acquisition.*not interpreted'):
            obj = data_object_from_xarray(array)
        assert obj.coordinates['acquisition'].unit is None
        assert (
            obj.metadata['xarray:coordinate_attributes']['acquisition'][
                'units'
            ]
            == unit
        )
        np.testing.assert_array_equal(
            obj.coordinates['acquisition'].read((slice(None),)), [0, 1, 2]
        )


def test_xarray_adapter_requires_nonempty_string_auxiliary_names():
    array = xr.DataArray(
        [3, 4],
        dims=('sample',),
        coords={'acquisition': ('sample', [1, 2])},
    )
    with pytest.raises(TypeError, match='field name must be a string'):
        data_object_from_xarray(array.rename({'acquisition': 1}))
    with pytest.raises(ValueError, match='field name must not be empty'):
        data_object_from_xarray(array.rename({'acquisition': ''}))


def test_data_array_name_can_also_name_an_auxiliary_coordinate():
    array = xr.DataArray(
        [3, 4],
        dims=('sample',),
        name='signal',
        coords={'signal': ('sample', [1, 2])},
    )
    obj = data_object_from_xarray(array)
    np.testing.assert_array_equal(
        obj.fields['signal'].read((slice(None),)), [3, 4]
    )
    np.testing.assert_array_equal(
        obj.coordinates['signal'].read((slice(None),)), [1, 2]
    )


def test_xarray_adapter_maps_data_array_attrs_to_field() -> None:
    array = xr.DataArray(
        np.ones(2),
        dims=('x',),
        attrs={'units': 'mm', '_FillValue': -999.0},
    )

    field = data_object_from_xarray(array).fields['data']
    overridden = data_object_from_xarray(
        array, field_unit='um', missing_value=-1.0
    ).fields['data']
    cleared = data_object_from_xarray(
        array, field_unit=None, missing_value=None
    ).fields['data']

    assert field.unit == 'mm'
    assert field.missing_value == -999.0
    assert overridden.unit == 'um'
    assert overridden.missing_value == -1.0
    assert cleared.unit is None
    assert cleared.missing_value is None


def test_xarray_adapter_expands_spatial_embedding_columns() -> None:
    array = xr.DataArray(
        np.zeros((2, 3, 4, 5)), dims=('time', 'z', 'y', 'x'), name='data'
    )
    specification = XarrayEmbedding(
        'scanner',
        [[0.0, 0.0, 0.6], [0.0, -0.8, 0.0], [2.0, 0.0, 0.0]],
        [10.0, 20.0, 30.0],
        ('z', 'y', 'x'),
        ('mm', 'mm', 'mm'),
        ('L', 'P', 'S'),
    )

    data_object = data_object_from_xarray(array, embedding=specification)

    embedding = data_object.embeddings[0]
    np.testing.assert_allclose(
        embedding.matrix,
        [
            [0.0, 0.0, 0.0, 0.6],
            [0.0, 0.0, -0.8, 0.0],
            [0.0, 2.0, 0.0, 0.0],
        ],
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )
    np.testing.assert_allclose(
        embedding.offset,
        [10.0, 20.0, 30.0],
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )
    assert tuple(axis.name for axis in embedding.target_frame.axes) == (
        'L',
        'P',
        'S',
    )


def test_xarray_adapter_accepts_five_tuple_embedding() -> None:
    array = xr.DataArray(np.zeros((2, 3)), dims=('y', 'x'))

    data_object = data_object_from_xarray(
        array,
        embedding=('world', np.eye(2), np.zeros(2), ('y', 'x'), ('mm', 'mm')),
    )

    embedding = data_object.embeddings[0]
    np.testing.assert_array_equal(embedding.matrix, np.eye(2))
    assert tuple(axis.name for axis in embedding.target_frame.axes) == (
        'y',
        'x',
    )


@pytest.mark.parametrize(
    ('target_frame', 'error', 'match'),
    [
        (object(), TypeError, 'must be a CoordinateFrame'),
        (
            CoordinateFrame('other', (('y', 'mm'), ('x', 'mm'))),
            ValueError,
            'name must match',
        ),
        (
            CoordinateFrame('world', (('row', 'mm'), ('column', 'mm'))),
            ValueError,
            'axes must match',
        ),
    ],
)
def test_xarray_adapter_validates_retained_target_frame(
    target_frame: Any, error: type[Exception], match: str
) -> None:
    array = xr.DataArray(np.zeros((2, 3)), dims=('y', 'x'))
    specification = XarrayEmbedding(
        'world',
        np.eye(2),
        np.zeros(2),
        ('y', 'x'),
        ('mm', 'mm'),
        target_frame=target_frame,
    )

    with pytest.raises(error, match=match):
        data_object_from_xarray(array, embedding=specification)


def test_xarray_adapter_does_not_materialize_dask_data() -> None:
    counted = CountingArray(np.arange(24).reshape((4, 6)))
    lazy = da.from_array(counted, chunks=(2, 3), asarray=False)
    array = xr.DataArray(lazy, dims=('y', 'x'), name='lazy')
    counted.regions.clear()

    data_object = data_object_from_xarray(array)

    source = data_object.fields['lazy'].source
    assert isinstance(source, ArraySource)
    assert source.array is lazy
    assert counted.regions == []

    np.testing.assert_array_equal(
        source.read((slice(1, 2), slice(2, 4))), [[8, 9]]
    )
    assert counted.regions


@pytest.mark.parametrize(
    ('kwargs', 'error', 'match'),
    [
        ({'units': ()}, TypeError, 'units must be a mapping'),
        (
            {'axis_roles': ()},
            TypeError,
            'axis_roles must be a mapping',
        ),
        (
            {'units': {'missing': 'mm'}},
            ValueError,
            'units contains unknown dimensions',
        ),
        (
            {'axis_roles': {'missing': AxisRole.LOCAL}},
            ValueError,
            'axis_roles contains unknown dimensions',
        ),
        (
            {'axis_roles': {'x': 'local'}},
            TypeError,
            'axis_roles values must be AxisRole',
        ),
        (
            {'axis_roles': {'x': AxisRole.COMPONENT}},
            ValueError,
            'component roles are not supported',
        ),
    ],
)
def test_xarray_adapter_rejects_invalid_axis_options(
    kwargs: dict[str, Any], error: type[Exception], match: str
) -> None:
    array = xr.DataArray(np.ones(2), dims=('x',))

    with pytest.raises(error, match=match):
        data_object_from_xarray(array, **kwargs)


def test_xarray_adapter_rejects_non_data_array() -> None:
    with pytest.raises(TypeError, match=r'must be an xarray\.DataArray'):
        data_object_from_xarray(np.ones(2))  # type: ignore[arg-type]


def test_xarray_adapter_rejects_unsupported_coordinate_dtype() -> None:
    array = xr.DataArray(
        np.ones(2), dims=('flag',), coords={'flag': np.array([True, False])}
    )

    with pytest.raises(TypeError, match='unsupported dtype bool'):
        data_object_from_xarray(array)


def test_xarray_adapter_rejects_coordinate_on_another_dimension() -> None:
    array = xr.DataArray(
        np.ones((2, 2)),
        dims=('x', 'y'),
        coords={'x': ('y', [1.0, 2.0])},
    )

    with pytest.raises(ValueError, match='one-dimensional over its dimension'):
        data_object_from_xarray(array)


@pytest.mark.parametrize(
    ('embedding', 'error', 'match'),
    [
        (
            ('world', np.eye(2), np.zeros(2), ('y', 'x')),
            ValueError,
            'must contain five items',
        ),
        (object(), TypeError, 'XarrayEmbedding or 5-tuple'),
        (
            XarrayEmbedding(
                'world', np.eye(2), np.zeros(2), 'yx', ('mm', 'mm')
            ),
            TypeError,
            'axis names must be a sequence',
        ),
        (
            XarrayEmbedding(
                'world', np.eye(2), np.zeros(2), ('x', 'x'), ('mm', 'mm')
            ),
            ValueError,
            'spatial axis names must be unique',
        ),
        (
            XarrayEmbedding(
                'world', np.eye(2), np.zeros(2), ('y', 'missing'), ('mm', 'mm')
            ),
            ValueError,
            r"^unknown domain axis 'missing'$",
        ),
        (
            XarrayEmbedding(
                'world', np.ones((2, 1)), np.zeros(2), ('y', 'x'), ('mm', 'mm')
            ),
            ValueError,
            'matrix columns must match',
        ),
        (
            XarrayEmbedding(
                'world',
                np.eye(2),
                np.zeros(2),
                ('y', 'x'),
                ('mm', 'mm'),
                ('L',),
            ),
            ValueError,
            'target axis names must match',
        ),
        (
            XarrayEmbedding(
                'world', np.eye(2), np.zeros(2), ('y', 'x'), ('mm',)
            ),
            ValueError,
            'target axis units must match',
        ),
    ],
)
def test_xarray_adapter_rejects_invalid_embedding(
    embedding: Any, error: type[Exception], match: str
) -> None:
    array = xr.DataArray(np.ones((2, 2)), dims=('y', 'x'))

    with pytest.raises(error, match=match):
        data_object_from_xarray(array, embedding=embedding)


def test_reference_frame_helper_builds_pirana_conformant_geometry() -> None:
    specification = embedding_from_reference_frame(
        ('TimePoint', 'EchoTime', 'z', 'y', 'x'), _reference_frame()
    )

    assert specification.target_frame_name == 'LPS:1.2.840.123'
    assert tuple(specification.target_axis_names or ()) == ('L', 'P', 'S')
    assert tuple(specification.target_axis_units) == ('mm', 'mm', 'mm')
    np.testing.assert_allclose(
        specification.matrix,
        np.diag([2.0, 0.8, 0.6]),
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )
    np.testing.assert_allclose(
        specification.offset,
        [10.0, 20.0, 30.0],
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )


def test_reference_frame_registry_shares_frames_across_adaptations() -> None:
    array = xr.DataArray(np.zeros((2, 3, 4)), dims=('z', 'y', 'x'))
    registry = FrameRegistry()

    first = data_object_from_xarray(
        array,
        embedding=embedding_from_reference_frame(
            array.dims, _reference_frame(), registry=registry
        ),
    )
    second = data_object_from_xarray(
        array,
        embedding=embedding_from_reference_frame(
            array.dims, _reference_frame(), registry=registry
        ),
    )

    assert (
        first.embeddings[0].target_frame is second.embeddings[0].target_frame
    )


def test_uidless_reference_frames_do_not_share_through_registry() -> None:
    array = xr.DataArray(np.zeros((2, 3, 4)), dims=('z', 'y', 'x'))
    registry = FrameRegistry()
    objects = [
        data_object_from_xarray(
            array,
            embedding=embedding_from_reference_frame(
                array.dims,
                _reference_frame(frame_of_reference_uid=None),
                registry=registry,
            ),
        )
        for _ in range(2)
    ]
    first_frame = objects[0].embeddings[0].target_frame
    second_frame = objects[1].embeddings[0].target_frame

    assert first_frame is not second_frame
    assert isinstance(
        CoordinateGraph().resolve(first_frame, second_frame), Unplaced
    )
    assert registry.frames == {}


def test_reference_frame_registries_are_identity_isolated() -> None:
    array = xr.DataArray(np.zeros((2, 3, 4)), dims=('z', 'y', 'x'))

    registered = [
        data_object_from_xarray(
            array,
            embedding=embedding_from_reference_frame(
                array.dims, _reference_frame(), registry=FrameRegistry()
            ),
        )
        for _ in range(2)
    ]
    unregistered = [
        data_object_from_xarray(
            array,
            embedding=embedding_from_reference_frame(
                array.dims, _reference_frame()
            ),
        )
        for _ in range(2)
    ]

    assert (
        registered[0].embeddings[0].target_frame
        is not registered[1].embeddings[0].target_frame
    )
    assert (
        unregistered[0].embeddings[0].target_frame
        is not unregistered[1].embeddings[0].target_frame
    )


def test_reference_frame_helper_rejects_invalid_registry() -> None:
    with pytest.raises(TypeError, match='FrameRegistry'):
        embedding_from_reference_frame(
            ('z', 'y', 'x'),
            _reference_frame(),
            registry=object(),  # type: ignore[arg-type]
        )


def test_reference_frame_helper_folds_absolute_first_slice_position() -> None:
    specification = embedding_from_reference_frame(
        ('z', 'y', 'x'),
        _reference_frame(slice_positions=(5.0, 6.5, 8.0)),
    )

    np.testing.assert_allclose(
        specification.matrix,
        np.diag([1.5, 0.8, 0.6]),
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )
    np.testing.assert_allclose(
        specification.offset,
        [15.0, 20.0, 30.0],
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )


def test_reference_frame_helper_preserves_permuted_basis_association() -> None:
    frame = _reference_frame(
        basis_vectors=((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
    )

    specification = embedding_from_reference_frame(('z', 'y', 'x'), frame)

    expected = np.array([[0.0, 0.0, 0.6], [0.0, 0.8, 0.0], [2.0, 0.0, 0.0]])
    np.testing.assert_allclose(
        specification.matrix, expected, rtol=0.0, atol=_GEOMETRY_ATOL
    )
    indices = np.array([2.0, 3.0, 4.0])
    expected_point = (
        np.asarray(frame.origin)
        + indices[0] * 2.0 * np.asarray(frame.basis_vectors[0])
        + indices[1] * 0.8 * np.asarray(frame.basis_vectors[1])
        + indices[2] * 0.6 * np.asarray(frame.basis_vectors[2])
    )
    np.testing.assert_allclose(
        expected @ indices + specification.offset,
        expected_point,
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )


def test_reference_frame_helper_preserves_negative_slice_direction() -> None:
    specification = embedding_from_reference_frame(
        ('z', 'y', 'x'),
        _reference_frame(slice_positions=(3.0, 1.0, -1.0)),
    )

    np.testing.assert_allclose(
        specification.matrix,
        np.diag([-2.0, 0.8, 0.6]),
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )
    np.testing.assert_allclose(
        specification.offset,
        [13.0, 20.0, 30.0],
        rtol=0.0,
        atol=_GEOMETRY_ATOL,
    )


def test_reference_frame_helper_allows_nonorthogonal_unit_basis() -> None:
    diagonal = np.sqrt(0.5)
    basis_vectors = (
        (1.0, 0.0, 0.0),
        (diagonal, diagonal, 0.0),
        (0.0, 0.0, 1.0),
    )

    specification = embedding_from_reference_frame(
        ('z', 'y', 'x'), _reference_frame(basis_vectors=basis_vectors)
    )

    expected = (
        np.asarray(basis_vectors) * np.array([2.0, 0.8, 0.6])[:, np.newaxis]
    ).T
    np.testing.assert_allclose(
        specification.matrix, expected, rtol=0.0, atol=_GEOMETRY_ATOL
    )


def test_reference_frame_helper_uses_last_three_domain_axis_names() -> None:
    specification = embedding_from_reference_frame(
        ('time', 'slice', 'row', 'column'), _reference_frame()
    )

    assert tuple(specification.spatial_axis_names) == (
        'slice',
        'row',
        'column',
    )


def test_reference_frame_helper_omits_missing_uid_from_frame_name() -> None:
    specification = embedding_from_reference_frame(
        ('z', 'y', 'x'), _reference_frame(frame_of_reference_uid=None)
    )

    assert specification.target_frame_name == 'LPS'


def test_reference_frame_helper_rejects_irregular_slice_spacing() -> None:
    frame = _reference_frame(slice_positions=(0.0, 1.5, 3.2))

    with pytest.raises(NotImplementedError, match='irregular slice spacing'):
        embedding_from_reference_frame(('z', 'y', 'x'), frame)


@pytest.mark.parametrize(
    ('axis_names', 'frame', 'error', 'match'),
    [
        ('zyx', _reference_frame(), TypeError, 'sequence of strings'),
        (
            ('y', 'x'),
            _reference_frame(),
            ValueError,
            'at least three axes',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(origin=(1.0, 2.0)),
            ValueError,
            r'origin must have shape \(3,\)',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(origin=(1.0, np.nan, 3.0)),
            ValueError,
            'origin must contain only finite',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(slice_positions=(1.0,)),
            ValueError,
            'at least two values',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(slice_positions=(0.0, np.inf)),
            ValueError,
            'slice_positions must contain only finite',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(basis_vectors=np.diag([1.0, 1.0, 2.0])),
            ValueError,
            'must contain unit vectors',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(
                basis_vectors=((1.0, 0.0, 0.0),) * 2 + ((0.0, 0.0, 1.0),)
            ),
            ValueError,
            'must form a nonsingular basis',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(in_plane_spacing=(0.8, 0.0)),
            ValueError,
            'in_plane_spacing values must be positive',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(slice_positions=(1.0, 1.0, 1.0)),
            ValueError,
            'slice spacing must be non-zero',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(convention='LP'),
            ValueError,
            'three-character string',
        ),
        (
            ('z', 'y', 'x'),
            _reference_frame(convention='LLS'),
            ValueError,
            'convention axes must be unique',
        ),
    ],
)
def test_reference_frame_helper_rejects_invalid_geometry(
    axis_names: Any,
    frame: SimpleNamespace,
    error: type[Exception],
    match: str,
) -> None:
    with pytest.raises(error, match=match):
        embedding_from_reference_frame(axis_names, frame)
