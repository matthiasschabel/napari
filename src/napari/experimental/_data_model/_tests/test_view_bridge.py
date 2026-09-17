import numpy as np
import pytest

from napari.components import Dims
from napari.experimental._data_model import (
    ArraySource,
    CoordinateAxis,
    CoordinateEmbedding,
    CoordinateFrame,
    DataObject,
    Field,
    Interpolation,
    MultiscaleSource,
    Polygon,
    PolygonSetDomain,
    StructuredGridDomain,
    add_to_viewer,
    layer_from_data_object,
    synthetic_mri,
)
from napari.experimental._data_model._view_bridge import (
    _SourceArray,
    _SourceLevelArray,
)
from napari.layers import Labels
from napari.layers.utils.layer_utils import (
    expand_corners_to_chunk_boundaries,
)


class ViewerLike:
    def __init__(self) -> None:
        self.layers: list[object] = []

    def add_layer(self, layer: object) -> None:
        self.layers.append(layer)


def test_raster_adapters_reject_partial_field_association():
    domain = StructuredGridDomain(
        (CoordinateAxis('measurement', 3), CoordinateAxis('x', 2))
    )
    obj = DataObject(
        'observations',
        domain,
        {
            'weight': Field(
                'weight', ArraySource(np.ones(3)), dimensions=('measurement',)
            ),
        },
    )
    viewer = ViewerLike()
    with pytest.raises(NotImplementedError, match='full-grid'):
        add_to_viewer(viewer, obj, field='weight')
    assert viewer.layers == []
    with pytest.raises(NotImplementedError, match='full-grid'):
        layer_from_data_object(obj, field='weight')
    full = Field(
        'signal', ArraySource(np.ones((3, 2))), dimensions=('measurement', 'x')
    )
    obj.update(fields={'signal': full})
    layer = add_to_viewer(viewer, obj, field='signal')
    assert layer.data.shape == (3, 2)
    assert (
        layer_from_data_object(obj, field='signal').data is full.source.array
    )


class CountingMultiscaleSource:
    def __init__(self, levels: tuple[np.ndarray, ...]) -> None:
        self._levels = levels
        self.shape = levels[0].shape
        self.dtype = levels[0].dtype
        self.levels = len(levels)
        self.reads: list[tuple[int, tuple[slice, ...]]] = []

    def level_shape(self, level: int) -> tuple[int, ...]:
        return self._levels[level].shape

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        self.reads.append((level, region))
        return self._levels[level][region]


class ChunkedArray:
    def __init__(self, data: np.ndarray, chunks: tuple[int, ...]) -> None:
        self._data = data
        self.shape = data.shape
        self.dtype = data.dtype
        self.chunks = chunks

    def __getitem__(self, region: tuple[slice, ...]) -> np.ndarray:
        return self._data[region]


def _source_array() -> _SourceArray:
    return _SourceArray(ArraySource(np.arange(6).reshape(2, 3)))


def test_source_array_rejects_multiple_ellipses() -> None:
    with pytest.raises(IndexError, match='single ellipsis'):
        _source_array()[..., ...]


def test_source_array_rejects_too_many_indices() -> None:
    with pytest.raises(IndexError, match='too many indices'):
        _source_array()[:, :, :]


def test_source_array_rejects_out_of_bounds_index() -> None:
    with pytest.raises(IndexError, match='out of bounds'):
        _source_array()[2]


def test_source_array_rejects_non_integer_or_slice_index() -> None:
    with pytest.raises(TypeError, match='integers or slices'):
        _source_array()[1.5]


def test_source_level_array_reads_selected_level_and_squeezes_indices() -> (
    None
):
    fine = np.arange(4 * 6, dtype=np.int16).reshape(4, 6)
    coarse = fine[::2, ::2].copy() + 1000
    source = MultiscaleSource((fine, coarse))
    level = _SourceLevelArray(source, 1)

    assert level.shape == (2, 3)
    assert level.ndim == 2
    assert level.size == 6
    assert level.dtype == np.dtype(np.int16)
    np.testing.assert_array_equal(level[1, ...], coarse[1, ...])


def test_source_level_array_array_materializes_the_complete_level() -> None:
    fine = np.arange(8 * 12, dtype=np.int16).reshape(8, 12)
    coarse = fine[::2, ::2].copy()
    source = CountingMultiscaleSource((fine, coarse))
    level = _SourceLevelArray(source, 1)

    result = np.asarray(level)

    np.testing.assert_array_equal(result, coarse)
    assert source.reads == [(1, (slice(0, 4, 1), slice(0, 6, 1)))]


def test_source_level_array_slice_view_materializes_composed_window() -> None:
    fine = np.arange(8 * 12, dtype=np.int16).reshape(8, 12)
    coarse = fine[::2, ::2].copy()
    source = CountingMultiscaleSource((fine, coarse))
    level = _SourceLevelArray(source, 1)

    view = level[1:4:2, 1:6:2]

    assert source.reads == []
    assert view.shape == (2, 3)
    result = np.asarray(view)

    np.testing.assert_array_equal(result, coarse[1:4:2, 1:6:2])
    assert source.reads == [(1, (slice(1, 4, 2), slice(1, 6, 2)))]


def test_source_level_array_slice_view_composes_point_index() -> None:
    data = np.arange(4 * 6, dtype=np.int16).reshape(4, 6)
    source = CountingMultiscaleSource((data,))
    level = _SourceLevelArray(source, 0)

    view = level[:, 1:6:2]
    result = view[2, 1:]

    np.testing.assert_array_equal(result, data[2, 3:6:2])
    assert source.reads == [(0, (slice(2, 3, 1), slice(3, 6, 2)))]


def test_source_level_array_exposes_chunks_for_corner_expansion() -> None:
    data = np.zeros((4, 10, 12), dtype=np.uint8)
    chunks = (2, 4, 5)
    source = MultiscaleSource((ChunkedArray(data, chunks),))
    level = _SourceLevelArray(source, 0)
    corners = np.array([[1, 3, 6], [1, 6, 8]])

    expanded = expand_corners_to_chunk_boundaries(corners, level, axes=(1, 2))
    view = level[:, 2:8, 1:10]

    assert level.chunks == chunks
    # A cropped view's chunk grid is offset by the crop origin, so region
    # views must not advertise the level chunks.
    assert getattr(view, 'chunks', None) is None
    np.testing.assert_array_equal(expanded, [[1, 0, 5], [1, 7, 9]])


def test_view_bridge_builds_native_multiscale_layer_at_level_zero_extent() -> (
    None
):
    fine = np.arange(8 * 6, dtype=np.uint16).reshape(8, 6)
    coarse = fine[::2, ::2].copy()
    source = MultiscaleSource((fine, coarse))
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 8), CoordinateAxis('x', 6))
    )
    world = CoordinateFrame('world', (('y', 'mm'), ('x', 'mm')))
    data_object = DataObject(
        'pyramid',
        domain,
        {'data': Field('data', source)},
        embeddings=(
            CoordinateEmbedding(domain, world, [[2.0, 0.0], [0.0, 3.0]]),
        ),
    )

    layer = add_to_viewer(ViewerLike(), data_object, 'data')

    assert layer.multiscale is True
    assert isinstance(layer.data_raw, list)
    assert [level.shape for level in layer.data_raw] == [(8, 6), (4, 3)]
    assert all(
        isinstance(level, _SourceLevelArray) for level in layer.data_raw
    )
    np.testing.assert_array_equal(layer.extent.data, [[0, 0], [7, 5]])
    np.testing.assert_allclose(layer.scale, [2.0, 3.0])


def test_view_bridge_multiscale_layer_switch_reads_selected_level() -> None:
    fine = np.arange(4 * 8 * 8, dtype=np.int16).reshape(4, 8, 8)
    coarse = fine[::2, ::2, ::2].copy() + 1000
    source = CountingMultiscaleSource((fine, coarse))
    domain = StructuredGridDomain(
        tuple(
            CoordinateAxis(name, size)
            for name, size in zip(('z', 'y', 'x'), fine.shape, strict=True)
        )
    )
    data_object = DataObject(
        'pyramid', domain, {'data': Field('data', source)}
    )
    layer = add_to_viewer(ViewerLike(), data_object, 'data')
    dims = Dims(ndim=3, ndisplay=2, point=(1, 0, 0))

    source.reads.clear()
    layer._data_level = 0
    layer.corner_pixels = np.array([[0, 0, 0], [0, 7, 7]])
    layer._slice_dims(dims, force=True)
    layer.set_view_slice()

    assert source.reads
    # The visible slice reads at the data level; the thumbnail adds one
    # point-sliced read at the coarsest level (never a full level).
    assert {level for level, _region in source.reads} == {0, 1}
    assert all(
        region[0] == slice(1, 2, 1)
        for level, region in source.reads
        if level == 0
    )
    assert all(
        len(range(*region[0].indices(coarse.shape[0]))) == 1
        for level, region in source.reads
        if level == 1
    )

    source.reads.clear()
    layer._data_level = 1
    layer.corner_pixels = np.array([[0, 0, 0], [0, 3, 3]])
    layer._slice_dims(dims, force=True)
    layer.set_view_slice()

    assert source.reads
    assert {level for level, _region in source.reads} == {1}
    assert all(
        len(range(*region[0].indices(coarse.shape[0]))) == 1
        for _level, region in source.reads
    )


def test_view_bridge_single_level_keeps_source_array_path() -> None:
    layer = add_to_viewer(ViewerLike(), synthetic_mri())

    assert layer.multiscale is False
    assert isinstance(layer.data, _SourceArray)


def test_view_bridge_rejects_invalid_viewer_shape() -> None:
    with pytest.raises(TypeError, match='must expose add_layer and layers'):
        add_to_viewer(object(), synthetic_mri())


def test_view_bridge_rejects_non_data_object() -> None:
    with pytest.raises(TypeError, match='obj must be a DataObject'):
        add_to_viewer(ViewerLike(), object())  # type: ignore[arg-type]


def test_view_bridge_rejects_unknown_field() -> None:
    with pytest.raises(KeyError, match="unknown field 'missing'"):
        add_to_viewer(ViewerLike(), synthetic_mri(), 'missing')


def test_view_bridge_requires_structured_grid_domain() -> None:
    polygon = Polygon(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]))
    data_object = DataObject(
        'geometry', PolygonSetDomain((polygon,), ('x', 'y'))
    )

    with pytest.raises(TypeError, match='requires a StructuredGridDomain'):
        add_to_viewer(ViewerLike(), data_object)


def test_view_bridge_rejects_complex_field() -> None:
    with pytest.raises(
        NotImplementedError,
        match=r'Image layers do not support complex data.*magnitude or phase',
    ):
        add_to_viewer(ViewerLike(), synthetic_mri(), 'signal')


def test_view_bridge_builds_labels_for_integer_nearest_field() -> None:
    values = np.arange(12, dtype=np.uint16).reshape(3, 4)
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 3), CoordinateAxis('x', 4))
    )
    data_object = DataObject(
        'segmentation',
        domain,
        {
            'labels': Field(
                'labels',
                ArraySource(values),
                interpolation=Interpolation.NEAREST,
            )
        },
    )
    viewer = ViewerLike()

    layer = add_to_viewer(viewer, data_object, 'labels')

    assert isinstance(layer, Labels)
    assert viewer.layers == [layer]
    assert layer.name == 'segmentation:labels'
    assert layer.metadata['napari:data_object'] is data_object


def test_view_bridge_uses_target_frame_units_for_spatial_axes() -> None:
    domain = StructuredGridDomain(
        (
            CoordinateAxis('time', 2, unit='s'),
            CoordinateAxis('y', 3, unit='mm'),
            CoordinateAxis('x', 4, unit='mm'),
        )
    )
    microscope = CoordinateFrame(
        'microscope', (('vertical', 'um'), ('horizontal', 'um'))
    )
    embedding = CoordinateEmbedding(
        domain,
        microscope,
        [[0.0, 2.0, 0.0], [0.0, 0.0, 3.0]],
        [5.0, 7.0],
    )
    data_object = DataObject(
        'image',
        domain,
        {'data': Field('data', ArraySource(np.ones(domain.shape)))},
        embeddings=(embedding,),
    )

    layer = add_to_viewer(ViewerLike(), data_object, 'data')

    np.testing.assert_allclose(layer.scale, [1.0, 2.0, 3.0])
    np.testing.assert_allclose(layer.translate, [0.0, 5.0, 7.0])
    assert tuple(str(unit) for unit in layer.units) == (
        'second',
        'micrometer',
        'micrometer',
    )


def test_view_bridge_keeps_domain_labels_with_permuted_embedding() -> None:
    domain = StructuredGridDomain(
        (
            CoordinateAxis('z', 2),
            CoordinateAxis('y', 3),
            CoordinateAxis('x', 4),
        )
    )
    scanner = CoordinateFrame(
        'scanner', (('L', 'um'), ('P', 'mm'), ('S', 'cm'))
    )
    embedding = CoordinateEmbedding(
        domain,
        scanner,
        [[0.0, 0.0, 0.6], [0.0, -0.8, 0.0], [2.0, 0.0, 0.0]],
        [5.0, -7.0, 11.0],
    )
    data_object = DataObject(
        'image',
        domain,
        {'data': Field('data', ArraySource(np.ones(domain.shape)))},
        embeddings=(embedding,),
    )

    layer = add_to_viewer(ViewerLike(), data_object, 'data')

    assert layer.axis_labels == ('z', 'y', 'x')
    np.testing.assert_allclose(layer.scale, [2.0, -0.8, 0.6])
    np.testing.assert_allclose(layer.translate, [11.0, -7.0, 5.0])
    assert tuple(str(unit) for unit in layer.units) == (
        'centimeter',
        'millimeter',
        'micrometer',
    )


def test_view_bridge_ignores_direction_cosine_residues() -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 3), CoordinateAxis('x', 4))
    )
    world = CoordinateFrame(
        'world', (('vertical', 'mm'), ('horizontal', 'um'))
    )
    embedding = CoordinateEmbedding(
        domain,
        world,
        [[1e-17, 2.0], [3.0, -1e-17]],
        [5.0, 7.0],
    )
    data_object = DataObject(
        'image',
        domain,
        {'data': Field('data', ArraySource(np.ones(domain.shape)))},
        embeddings=(embedding,),
    )

    layer = add_to_viewer(ViewerLike(), data_object, 'data')

    np.testing.assert_allclose(layer.scale, [3.0, 2.0])
    np.testing.assert_allclose(layer.translate, [7.0, 5.0])
    assert tuple(str(unit) for unit in layer.units) == (
        'micrometer',
        'millimeter',
    )


def test_view_bridge_embedding_overrides_spatial_axis_values() -> None:
    domain = StructuredGridDomain(
        (
            CoordinateAxis('y', 2),
            CoordinateAxis(
                'x', 3, unit='s', values=np.array([10.0, 12.0, 14.0])
            ),
        )
    )
    world = CoordinateFrame('world', (('x', 'mm'),))
    embedding = CoordinateEmbedding(domain, world, [[0.0, 3.0]], [7.0])
    data_object = DataObject(
        'placed',
        domain,
        {'data': Field('data', ArraySource(np.ones(domain.shape)))},
        embeddings=(embedding,),
    )

    layer = add_to_viewer(ViewerLike(), data_object, 'data')

    np.testing.assert_allclose(layer.scale, [1.0, 3.0])
    np.testing.assert_allclose(layer.translate, [0.0, 7.0])
    assert tuple(str(unit) for unit in layer.units) == (
        'pixel',
        'millimeter',
    )


def test_view_bridge_rejects_multiple_embeddings() -> None:
    domain = StructuredGridDomain((CoordinateAxis('x', 2),))
    world = CoordinateFrame('world', (('x', None),))
    first = CoordinateEmbedding(domain, world, [[1.0]])
    second = CoordinateEmbedding(domain, world, [[2.0]])
    data_object = DataObject(
        'placed twice',
        domain,
        {'data': Field('data', ArraySource(np.ones(domain.shape)))},
        embeddings=(first, second),
    )

    with pytest.raises(
        NotImplementedError, match='multiple coordinate embeddings'
    ):
        add_to_viewer(ViewerLike(), data_object, 'data')


@pytest.mark.parametrize(
    'matrix',
    [
        [[1.0, 0.25], [0.0, 1.0]],
        [[1.0, 0.0], [2.0, 0.0]],
        [[0.0, 0.0], [0.0, 1.0]],
    ],
)
def test_view_bridge_rejects_coupled_embedding(
    matrix: list[list[float]],
) -> None:
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 3), CoordinateAxis('x', 4))
    )
    world = CoordinateFrame('world', (('y', None), ('x', None)))
    embedding = CoordinateEmbedding(domain, world, matrix)
    data_object = DataObject(
        'coupled',
        domain,
        {'data': Field('data', ArraySource(np.ones(domain.shape)))},
        embeddings=(embedding,),
    )

    with pytest.raises(NotImplementedError, match='off-diagonal coupling'):
        add_to_viewer(ViewerLike(), data_object, 'data')


def test_source_region_view_rejects_non_integer_slice_bounds() -> None:
    data = np.zeros((4, 4), dtype=np.uint8)
    level = _SourceLevelArray(MultiscaleSource((data,)), 0)
    with pytest.raises(TypeError, match='bounds and steps must be integers'):
        level[slice(0, 2.5), :]


def test_source_region_view_rejects_negative_step() -> None:
    data = np.zeros((4, 4), dtype=np.uint8)
    level = _SourceLevelArray(MultiscaleSource((data,)), 0)
    with pytest.raises(ValueError, match='negative steps'):
        level[::-1, :]


def _scalar_object(
    values: np.ndarray, interpolation: Interpolation
) -> DataObject:
    domain = StructuredGridDomain(
        (
            CoordinateAxis('y', values.shape[0]),
            CoordinateAxis('x', values.shape[1]),
        )
    )
    return DataObject(
        'sample',
        domain,
        {
            'values': Field(
                'values',
                ArraySource(values),
                interpolation=interpolation,
            )
        },
    )


def test_view_bridge_shows_a_continuous_field_with_linear_interpolation() -> (
    None
):
    values = np.arange(12, dtype=np.float32).reshape(3, 4)
    data_object = _scalar_object(values, Interpolation.LINEAR)

    layer = add_to_viewer(ViewerLike(), data_object, 'values')

    assert layer.interpolation2d == 'linear'


def test_view_bridge_shows_a_categorical_field_with_nearest() -> None:
    # Nearest with a float dtype stays an Image layer, so the policy has
    # to reach the layer rather than riding on the Labels type.
    values = np.arange(12, dtype=np.float32).reshape(3, 4)
    data_object = _scalar_object(values, Interpolation.NEAREST)

    layer = add_to_viewer(ViewerLike(), data_object, 'values')

    assert layer.interpolation2d == 'nearest'


def test_display_read_retries_one_announced_change():
    from napari.experimental._data_model import SourceChangedError

    class ChangingSource(ArraySource):
        changes = 0

        def read(self, region, *, level=0):
            if self.changes:
                self.changes -= 1
                raise SourceChangedError('changed')
            return super().read(region, level=level)

    source = ChangingSource(np.arange(12).reshape(3, 4))
    domain = StructuredGridDomain(
        (CoordinateAxis('y', 3), CoordinateAxis('x', 4))
    )
    obj = DataObject('signal', domain, {'signal': Field('signal', source)})
    layer = add_to_viewer(ViewerLike(), obj, field='signal')
    source.changes = 1
    np.testing.assert_array_equal(np.asarray(layer.data), source.array)
    source.changes = 2
    with pytest.raises(SourceChangedError):
        np.asarray(layer.data)


def test_display_requires_scalar_component_of_physical_values():
    from napari.experimental._data_model import FieldGeometry

    domain = StructuredGridDomain(
        (CoordinateAxis('x', 2), CoordinateAxis('y', 2))
    )
    field = Field(
        'velocity',
        ArraySource(np.ones((2, 2, 3))),
        component_axes=(2,),
        geometry=FieldGeometry('vector', components=('x', 'y', 'z')),
    )
    obj = DataObject('flow', domain, {'velocity': field})
    viewer = ViewerLike()
    with pytest.raises(NotImplementedError, match='selected scalar component'):
        add_to_viewer(viewer, obj, field='velocity')
    assert viewer.layers == []


def test_display_samples_declared_shift_and_preserves_override_geometry():
    from napari.experimental._data_model import LevelGeometry

    fine = np.arange(64).reshape(8, 8)
    coarse = np.arange(100, 116).reshape(4, 4)
    declared = (LevelGeometry((1, 1)), LevelGeometry((2, 2), (1.25, 0)))
    source = MultiscaleSource((fine, coarse), level_geometries=declared)
    obj = DataObject(
        'shifted',
        StructuredGridDomain((CoordinateAxis('y', 8), CoordinateAxis('x', 8))),
        {'value': Field('value', source)},
    )
    layer = add_to_viewer(ViewerLike(), obj, field='value')
    expected = np.concatenate((fine[0:1, ::2], coarse[:3]), axis=0)
    np.testing.assert_array_equal(np.asarray(layer.data[1]), expected)
    override = MultiscaleSource((fine, coarse))
    with pytest.raises(ValueError, match='level geometry must match'):
        add_to_viewer(ViewerLike(), obj, field='value', source=override)
