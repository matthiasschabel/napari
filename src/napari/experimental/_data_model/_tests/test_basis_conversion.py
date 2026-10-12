import numpy as np
import pytest

from napari.experimental._data_model import (
    ArraySource,
    CoordinateFrame,
    Field,
    FieldGeometry,
    convert_field_basis,
)


def test_tensor_oracle_and_component_reads():
    source_frame = CoordinateFrame(
        'scanner', (('x', 'mm'), ('y', 'mm'), ('z', 'mm'))
    )
    target_frame = CoordinateFrame(
        'patient', (('R', 'm'), ('A', 'm'), ('S', 'm'))
    )
    angle = np.pi / 4
    c, s = np.cos(angle), np.sin(angle)
    q = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    tensor = np.diag([3.0, 1.0, -0.2])
    field = Field(
        'diffusion',
        ArraySource(np.stack([tensor, tensor * 2])),
        unit='mm**2/s',
        component_axes=(1, 2),
        geometry=FieldGeometry(
            'symmetric_tensor', basis_frame=source_frame, basis='orthonormal'
        ),
    )
    rotated = convert_field_basis(field, target_frame, q)
    actual = rotated.read((slice(None), slice(None), slice(None)))
    expected = np.array([[2.0, 1.0, 0.0], [1.0, 2.0, 0.0], [0.0, 0.0, -0.2]])
    np.testing.assert_allclose(actual[0], expected, atol=1e-12, rtol=0)
    np.testing.assert_allclose(
        np.linalg.eigvalsh(actual[0]), [-0.2, 1, 3], atol=1e-12, rtol=0
    )
    np.testing.assert_allclose(
        rotated.read((slice(0, 1), slice(0, 1), slice(1, 2))),
        [[[1.0]]],
        atol=1e-12,
        rtol=0,
    )
    restored = convert_field_basis(rotated, source_frame, q.T)
    np.testing.assert_allclose(
        restored.read((slice(None), slice(None), slice(None))),
        np.stack([tensor, tensor * 2]),
        atol=1e-12,
        rtol=0,
    )
    assert rotated.unit == field.unit


def test_packed_tensor_and_velocity_preserve_value_magnitude():
    frame = CoordinateFrame('xy', (('x', 'mm'), ('y', 'mm')))
    target = CoordinateFrame('uv', (('u', 'mm'), ('v', 'mm')))
    q = np.array([[0.0, -1.0], [1.0, 0.0]])
    packed = Field(
        'D',
        ArraySource(np.array([[3.0, 1.0, 0.5]])),
        component_axes=(1,),
        geometry=FieldGeometry(
            'symmetric_tensor',
            basis_frame=frame,
            basis='orthonormal',
            packing=((0, 0), (1, 1), (0, 1)),
        ),
    )
    actual = convert_field_basis(packed, target, q).read(
        (slice(None), slice(None))
    )
    np.testing.assert_allclose(actual, [[1.0, 3.0, -0.5]], atol=1e-12, rtol=0)
    velocity = Field(
        'v',
        ArraySource(np.array([[3.0, -999.0], [4.0, 5.0]])),
        component_axes=(0,),
        missing_value=-999.0,
        unit='mm/s',
        geometry=FieldGeometry(
            'vector', basis_frame=frame, basis='orthonormal'
        ),
    )
    actual = convert_field_basis(velocity, target, q).read(
        (slice(None), slice(None))
    )
    np.testing.assert_allclose(
        actual, [[-4.0, -999.0], [3.0, -999.0]], atol=1e-12, rtol=0
    )
    assert np.linalg.norm(actual[:, 0]) == pytest.approx(5.0, abs=1e-12)


def test_rejects_untyped_bundles_scale_shear_and_incompatible_frames():
    frame = CoordinateFrame('space', (('x', 'mm'), ('y', 'mm')))
    field = Field(
        'v',
        ArraySource(np.ones((4, 2))),
        component_axes=(1,),
        geometry=FieldGeometry(
            'vector', basis_frame=frame, basis='orthonormal'
        ),
    )
    for matrix in (
        np.diag([2, 1]),
        np.array([[1, 1], [0, 1]]),
        np.full((2, 2), np.nan),
    ):
        with pytest.raises(ValueError, match='orthogonal'):
            convert_field_basis(field, frame, matrix)
    target = CoordinateFrame('mixed', (('x', 'mm'), ('t', 's')))
    with pytest.raises(ValueError, match='compatible'):
        convert_field_basis(field, target, np.eye(2))
    for geometry in (
        None,
        FieldGeometry('components', components=('coil1', 'coil2')),
        FieldGeometry('vector', basis_frame=frame),
    ):
        source = Field(
            'values',
            ArraySource(np.ones((4, 2))),
            component_axes=(1,),
            geometry=geometry,
        )
        with pytest.raises(ValueError, match='explicitly orthonormal'):
            convert_field_basis(source, frame, np.eye(2))


def test_lazy_conversion_expands_components_but_keeps_sample_region_bounded():
    class Recording(ArraySource):
        def __init__(self):
            super().__init__(np.arange(200).reshape(100, 2))
            self.regions = []

        def read(self, region, *, level=0):
            self.regions.append(region)
            return super().read(region, level=level)

    source = Recording()
    frame = CoordinateFrame('space', (('x', 'mm'), ('y', 'mm')))
    field = Field(
        'v',
        source,
        component_axes=(1,),
        geometry=FieldGeometry(
            'vector', basis_frame=frame, basis='orthonormal'
        ),
    )
    result = convert_field_basis(field, frame, np.array([[0, -1], [1, 0]]))
    assert source.regions == []
    np.testing.assert_allclose(
        result.read((slice(99, 100), slice(0, 1))),
        [[-199]],
        atol=1e-12,
        rtol=0,
    )
    assert source.regions == [(slice(99, 100, 1), slice(None))]


def test_basis_conversion_validates_requests_and_announced_changes():
    from napari.experimental._data_model import SourceChangedError

    frame = CoordinateFrame('xy', (('x', 'mm'), ('y', 'mm')))
    field = Field(
        'v',
        ArraySource(np.ones((3, 2))),
        component_axes=(1,),
        geometry=FieldGeometry(
            'vector', basis_frame=frame, basis='orthonormal'
        ),
    )
    with pytest.raises(TypeError, match='Field'):
        convert_field_basis('v', frame, np.eye(2))
    with pytest.raises(TypeError, match='CoordinateFrame'):
        convert_field_basis(field, 'xy', np.eye(2))
    with pytest.raises(TypeError, match='real numeric'):
        convert_field_basis(field, frame, np.eye(2, dtype=complex))
    with pytest.raises(ValueError, match='component count'):
        convert_field_basis(field, frame, np.eye(3))
    from dataclasses import replace

    with pytest.raises(ValueError, match='scalar missing'):
        convert_field_basis(
            replace(field, missing_value=[0, 1]), frame, np.eye(2)
        )
    with pytest.raises(TypeError, match='real numeric scalars'):
        convert_field_basis(
            replace(field, missing_value='bad'), frame, np.eye(2)
        )

    class ChangingSource(ArraySource):
        def read(self, region, *, level=0):
            result = super().read(region, level=level)
            self.invalidate()
            return result

    field = replace(field, source=ChangingSource(np.ones((3, 2))))
    result = convert_field_basis(field, frame, np.eye(2))
    with pytest.raises(SourceChangedError, match='basis conversion'):
        result.read((slice(None), slice(None)))


def test_basis_conversion_preserves_nan_invalid_values():
    frame = CoordinateFrame('xy', (('x', 'mm'), ('y', 'mm')))
    field = Field(
        'v',
        ArraySource(np.array([[np.nan, 1.0]])),
        component_axes=(1,),
        missing_value=np.nan,
        geometry=FieldGeometry(
            'vector', basis_frame=frame, basis='orthonormal'
        ),
    )
    result = convert_field_basis(field, frame, np.eye(2)).read(
        (slice(None), slice(None))
    )
    assert np.isnan(result).all()


def test_multilevel_basis_conversion_preserves_geometry_and_full_tensor_missing_values():

    from napari.experimental._data_model import (
        LevelGeometry,
        MultiscaleSource,
        source_level_geometry,
    )

    frame = CoordinateFrame('xy', (('x', 'mm'), ('y', 'mm')))
    source = MultiscaleSource(
        (
            np.ones((4, 2, 2)),
            np.array(
                [[[3.0, -999.0], [-999.0, 1.0]], [[3.0, 0.0], [0.0, 1.0]]]
            ),
        ),
        level_geometries=(
            LevelGeometry((1, 1, 1)),
            LevelGeometry((2, 1, 1), (1, 0, 0)),
        ),
    )
    field = Field(
        'tensor',
        source,
        component_axes=(1, 2),
        missing_value=-999.0,
        geometry=FieldGeometry(
            'symmetric_tensor', basis_frame=frame, basis='orthonormal'
        ),
    )
    converted = convert_field_basis(field, frame, np.array([[0, -1], [1, 0]]))
    assert source_level_geometry(converted.source, 1).matches(
        source_level_geometry(source, 1)
    )
    actual = converted.read((slice(None), slice(None), slice(None)), level=1)
    np.testing.assert_allclose(
        actual,
        [[[-999.0, -999.0], [-999.0, -999.0]], [[1.0, 0.0], [0.0, 3.0]]],
        rtol=0,
        atol=1e-12,
    )
