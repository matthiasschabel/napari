import numpy as np
import pytest

from napari.experimental._data_model import (
    ArraySource,
    DerivedSource,
    LevelGeometry,
    MultiscaleSource,
    source_level_geometry,
)


def test_declared_geometry_survives_cache_and_derivation_without_shape_guessing():
    source = MultiscaleSource(
        (np.zeros((275, 61)), np.zeros((137, 20))),
        level_geometries=(
            LevelGeometry((1, 1)),
            LevelGeometry((2, 3), (0.5, 1)),
        ),
    )
    for wrapped in (source, DerivedSource(source, np.negative)):
        declared = source_level_geometry(wrapped, 1)
        np.testing.assert_allclose(
            declared.to_base([[136, 19]]), [[272.5, 58]], rtol=0, atol=1e-12
        )
        assert wrapped.read((slice(0, 1), slice(0, 1)), level=1).shape == (
            1,
            1,
        )
    unknown = MultiscaleSource((np.zeros(275), np.zeros(137)))
    assert source_level_geometry(unknown, 1) is None
    np.testing.assert_allclose(
        source_level_geometry(ArraySource(np.zeros(3)), 0).to_base([[2]]),
        [[2]],
        rtol=0,
        atol=1e-12,
    )


@pytest.mark.parametrize(
    ('geometry', 'error'),
    [
        ((LevelGeometry((1,)),), ValueError),
        ((LevelGeometry((1,)), 'two'), TypeError),
        ((LevelGeometry((1,)), LevelGeometry((2, 2))), ValueError),
        ((LevelGeometry((2,)), LevelGeometry((2,))), ValueError),
    ],
)
def test_multiscale_geometry_validation(geometry, error):
    with pytest.raises(error):
        MultiscaleSource((np.zeros(4), np.zeros(2)), level_geometries=geometry)


def test_declared_level_geometry_is_independent_of_odd_shapes():
    level = LevelGeometry((2.0, 3.0), (0.5, 1.0))
    np.testing.assert_allclose(
        level.to_base([[0, 0], [136, 20]]),
        [[0.5, 1.0], [272.5, 61.0]],
        atol=1e-12,
        rtol=0,
    )
    # A base length of 275 and a stored length of 137 do not change the declared factor two.
    assert level.scale[0] == pytest.approx(2.0, abs=1e-12)


@pytest.mark.parametrize(
    ('scale', 'offset', 'error'),
    [
        ((0,), (), ValueError),
        ((-1,), (), ValueError),
        ((True,), (), TypeError),
        ((float('inf'),), (), ValueError),
        ((1,), (0, 0), ValueError),
        (('1',), (), TypeError),
    ],
)
def test_invalid_level_geometry(scale, offset, error):
    with pytest.raises(error):
        LevelGeometry(scale, offset)


def test_foreign_level_geometry_capability_is_validated():
    class Declared(ArraySource):
        declaration = 'invalid'

        def level_geometry(self, level):
            return self.declaration

    source = Declared(np.zeros(4))
    with pytest.raises(TypeError, match='LevelGeometry or None'):
        source_level_geometry(source, 0)
    source.declaration = LevelGeometry((1, 1))
    with pytest.raises(ValueError, match='rank'):
        source_level_geometry(source, 0)
    source.declaration = LevelGeometry((2,))
    with pytest.raises(ValueError, match='identity'):
        source_level_geometry(source, 0)
    source.level_geometry = 3
    with pytest.raises(TypeError, match='callable'):
        source_level_geometry(source, 0)
    with pytest.raises(ValueError, match='index vectors'):
        LevelGeometry((1,)).to_base([[1, 2]])


def test_components_are_not_downsampled_or_shifted():
    from napari.experimental._data_model import Field, FieldGeometry

    geometry = FieldGeometry('vector', components=('x', 'y'))
    source = MultiscaleSource((np.ones((4, 2)), np.ones((2, 1))))
    with pytest.raises(ValueError, match='cardinalities'):
        Field('v', source, component_axes=(1,), geometry=geometry)
    source = MultiscaleSource(
        (np.ones((4, 2)), np.ones((2, 2))),
        level_geometries=(LevelGeometry((1, 1)), LevelGeometry((2, 2))),
    )
    with pytest.raises(ValueError, match='identity level geometry'):
        Field('v', source, component_axes=(1,), geometry=geometry)


def test_declared_pyramid_scales_are_monotone():
    with pytest.raises(ValueError, match='monotonically'):
        MultiscaleSource(
            (np.zeros(8), np.zeros(4), np.zeros(2)),
            level_geometries=(
                LevelGeometry((1,)),
                LevelGeometry((4,)),
                LevelGeometry((2,)),
            ),
        )
