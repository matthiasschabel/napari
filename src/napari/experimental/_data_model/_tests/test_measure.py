import numpy as np
import pytest

from napari.experimental._data_model import (
    ROI,
    CoordinateFrame,
    CoordinateSelection,
    DataObject,
    PlaneAnchor,
    Polygon,
    PolygonSetDomain,
    mean_over_roi,
    synthetic_mri,
)


def _synthetic_world_coordinates(
    data_object: DataObject, axis_name: str
) -> np.ndarray:
    embedding = data_object.embeddings[0]
    target_axis = next(
        index
        for index, axis in enumerate(embedding.target_frame.axes)
        if axis.name == axis_name
    )
    domain_axis = data_object.domain.axis_index(axis_name)
    return embedding.offset[target_axis] + embedding.matrix[
        target_axis, domain_axis
    ] * np.arange(data_object.domain.axes[domain_axis].size)


def _pixel_center_bounds(
    coordinates: np.ndarray, first: int, last: int
) -> tuple[float, float]:
    lower = (coordinates[first - 1] + coordinates[first]) / 2
    upper = (coordinates[last] + coordinates[last + 1]) / 2
    return (float(lower), float(upper))


def _rectangle(
    first_bounds: tuple[float, float],
    second_bounds: tuple[float, float],
) -> np.ndarray:
    first_min, first_max = first_bounds
    second_min, second_max = second_bounds
    return np.array(
        [
            [first_min, second_min],
            [first_max, second_min],
            [first_max, second_max],
            [first_min, second_max],
        ]
    )


def _make_synthetic_roi(
    data_object: DataObject,
    polygon: Polygon | tuple[Polygon, ...],
    *,
    plane_value: float | None = None,
    context: dict[str, float] | None = None,
    frame: CoordinateFrame | None = None,
) -> ROI:
    embedding = data_object.embeddings[0]
    anchor_frame = embedding.target_frame if frame is None else frame
    frame_axes = {axis.name: axis for axis in anchor_frame.axes}
    polygons = (polygon,) if isinstance(polygon, Polygon) else polygon
    geometry = DataObject(
        'ROI geometry',
        PolygonSetDomain(
            polygons,
            ('y', 'x'),
            (frame_axes['y'].unit, frame_axes['x'].unit),
        ),
    )
    if plane_value is None:
        plane_value = float(_synthetic_world_coordinates(data_object, 'z')[8])
    if context is None:
        context = {'time': 2.0, 'echo_time': 9.0, 'flip_angle': 15.0}
    return ROI(
        'synthetic ROI',
        geometry,
        PlaneAnchor(
            anchor_frame,
            'z',
            plane_value,
            ('y', 'x'),
            context,
        ),
        data_object,
    )


def test_mean_over_roi_matches_synthetic_echo_decay() -> None:
    data_object = synthetic_mri()
    embedding = data_object.embeddings[0]
    z_index = 8
    y_index = 12
    x_index = 10
    z_world, y_world, x_world = embedding.map_points(
        [[0.0, 0.0, 0.0, z_index, y_index, x_index]],
        frame=data_object.domain.intrinsic_frame,
    )[0]
    exterior = np.array(
        [
            [y_world - 0.2, x_world - 0.2],
            [y_world + 0.2, x_world - 0.2],
            [y_world + 0.2, x_world + 0.2],
            [y_world - 0.2, x_world + 0.2],
        ]
    )
    geometry = DataObject(
        'single pixel geometry',
        PolygonSetDomain((Polygon(exterior),), ('y', 'x'), ('mm', 'mm')),
    )
    anchor = PlaneAnchor(
        embedding.target_frame,
        'z',
        z_world,
        ('y', 'x'),
        {'time': 0.0, 'flip_angle': 15.0},
    )
    roi = ROI('single pixel', geometry, anchor, data_object)

    means = mean_over_roi(
        data_object, roi, 'magnitude', sample_axes=('echo_time',)
    )

    z = np.linspace(-1.0, 1.0, 16)[z_index]
    y = np.linspace(-1.0, 1.0, 24)[y_index]
    x = np.linspace(-1.0, 1.0, 24)[x_index]
    t2_star = 18.0 + 12.0 * (1.0 - (z**2 + y**2 + x**2) / 3.0)
    spatial_magnitude = 1.0 + 0.15 * x + 0.08 * y
    echo_times = np.array([2.0, 4.5, 9.0, 15.0, 22.5])
    expected = (
        np.exp(-echo_times / t2_star)
        * np.sin(np.deg2rad(15.0))
        * spatial_magnitude
    )

    np.testing.assert_allclose(list(means), echo_times, rtol=0.0, atol=0.0)
    np.testing.assert_allclose(
        list(means.values()), expected, rtol=1e-6, atol=1e-7
    )

    selected = CoordinateSelection(
        {'time': 0, 'flip_angle': 1, 'z': z_index, 'y': y_index, 'x': x_index}
    ).read(data_object, 'magnitude')
    np.testing.assert_allclose(
        list(means.values()), selected, rtol=1e-6, atol=1e-7
    )


def test_mean_over_roi_matches_independent_rectangular_mask() -> None:
    data_object = synthetic_mri()
    y_coordinates = _synthetic_world_coordinates(data_object, 'y')
    x_coordinates = _synthetic_world_coordinates(data_object, 'x')
    y_bounds = _pixel_center_bounds(y_coordinates, 6, 16)
    x_bounds = _pixel_center_bounds(x_coordinates, 5, 18)
    roi = _make_synthetic_roi(
        data_object, Polygon(_rectangle(y_bounds, x_bounds))
    )

    means = mean_over_roi(data_object, roi, 'magnitude')

    y_grid, x_grid = np.meshgrid(y_coordinates, x_coordinates, indexing='ij')
    mask = (
        (y_grid >= y_bounds[0])
        & (y_grid <= y_bounds[1])
        & (x_grid >= x_bounds[0])
        & (x_grid <= x_bounds[1])
    )
    magnitude = np.abs(data_object.fields['signal'].source.array[1, 2, 1, 8])
    assert means == {(): pytest.approx(float(np.mean(magnitude[mask])))}


def test_mean_over_roi_excludes_pixel_centers_on_exterior_edge() -> None:
    data_object = synthetic_mri()
    y_coordinates = _synthetic_world_coordinates(data_object, 'y')
    x_coordinates = _synthetic_world_coordinates(data_object, 'x')
    y_bounds = (
        float(y_coordinates[6]),
        _pixel_center_bounds(y_coordinates, 6, 16)[1],
    )
    x_bounds = _pixel_center_bounds(x_coordinates, 5, 18)
    roi = _make_synthetic_roi(
        data_object, Polygon(_rectangle(y_bounds, x_bounds))
    )

    means = mean_over_roi(data_object, roi, 'magnitude')

    y_grid, x_grid = np.meshgrid(y_coordinates, x_coordinates, indexing='ij')
    mask = (
        (y_grid > y_bounds[0])
        & (y_grid < y_bounds[1])
        & (x_grid > x_bounds[0])
        & (x_grid < x_bounds[1])
    )
    inclusive_mask = (
        (y_grid >= y_bounds[0])
        & (y_grid < y_bounds[1])
        & (x_grid > x_bounds[0])
        & (x_grid < x_bounds[1])
    )
    magnitude = np.abs(data_object.fields['signal'].source.array[1, 2, 1, 8])
    expected = float(np.mean(magnitude[mask]))
    inclusive_mean = float(np.mean(magnitude[inclusive_mask]))
    assert not np.any(mask[6])
    assert not np.isclose(expected, inclusive_mean, rtol=1e-12, atol=0.0)
    assert means == {(): pytest.approx(expected)}


def test_mean_over_roi_excludes_hole_pixels() -> None:
    data_object = synthetic_mri()
    y_coordinates = _synthetic_world_coordinates(data_object, 'y')
    x_coordinates = _synthetic_world_coordinates(data_object, 'x')
    exterior_bounds = (
        _pixel_center_bounds(y_coordinates, 4, 19),
        _pixel_center_bounds(x_coordinates, 3, 20),
    )
    hole_bounds = (
        _pixel_center_bounds(y_coordinates, 9, 14),
        _pixel_center_bounds(x_coordinates, 8, 15),
    )
    roi = _make_synthetic_roi(
        data_object,
        Polygon(
            _rectangle(*exterior_bounds),
            (_rectangle(*hole_bounds),),
        ),
    )

    means = mean_over_roi(data_object, roi, 'magnitude')

    y_grid, x_grid = np.meshgrid(y_coordinates, x_coordinates, indexing='ij')
    exterior_mask = (
        (y_grid >= exterior_bounds[0][0])
        & (y_grid <= exterior_bounds[0][1])
        & (x_grid >= exterior_bounds[1][0])
        & (x_grid <= exterior_bounds[1][1])
    )
    hole_mask = (
        (y_grid >= hole_bounds[0][0])
        & (y_grid <= hole_bounds[0][1])
        & (x_grid >= hole_bounds[1][0])
        & (x_grid <= hole_bounds[1][1])
    )
    magnitude = np.abs(data_object.fields['signal'].source.array[1, 2, 1, 8])
    expected = float(np.mean(magnitude[exterior_mask & ~hole_mask]))
    assert means == {(): pytest.approx(expected)}


def test_mean_over_roi_includes_island_pixels_inside_hole() -> None:
    data_object = synthetic_mri()
    y_coordinates = _synthetic_world_coordinates(data_object, 'y')
    x_coordinates = _synthetic_world_coordinates(data_object, 'x')
    exterior_bounds = (
        _pixel_center_bounds(y_coordinates, 4, 19),
        _pixel_center_bounds(x_coordinates, 3, 20),
    )
    hole_bounds = (
        _pixel_center_bounds(y_coordinates, 7, 16),
        _pixel_center_bounds(x_coordinates, 6, 17),
    )
    island_bounds = (
        _pixel_center_bounds(y_coordinates, 10, 13),
        _pixel_center_bounds(x_coordinates, 10, 13),
    )
    outer = Polygon(_rectangle(*exterior_bounds), (_rectangle(*hole_bounds),))
    island = Polygon(_rectangle(*island_bounds))
    roi = _make_synthetic_roi(data_object, (outer, island))

    means = mean_over_roi(data_object, roi, 'magnitude')

    y_grid, x_grid = np.meshgrid(y_coordinates, x_coordinates, indexing='ij')

    def rectangular_mask(
        bounds: tuple[tuple[float, float], tuple[float, float]],
    ) -> np.ndarray:
        return (
            (y_grid >= bounds[0][0])
            & (y_grid <= bounds[0][1])
            & (x_grid >= bounds[1][0])
            & (x_grid <= bounds[1][1])
        )

    mask = rectangular_mask(exterior_bounds) & (
        ~rectangular_mask(hole_bounds) | rectangular_mask(island_bounds)
    )
    magnitude = np.abs(data_object.fields['signal'].source.array[1, 2, 1, 8])
    assert means == {(): pytest.approx(float(np.mean(magnitude[mask])))}


def test_mean_over_roi_resolves_context_exactly() -> None:
    data_object = synthetic_mri()
    y_coordinates = _synthetic_world_coordinates(data_object, 'y')
    x_coordinates = _synthetic_world_coordinates(data_object, 'x')
    roi = _make_synthetic_roi(
        data_object,
        Polygon(
            _rectangle(
                _pixel_center_bounds(y_coordinates, 6, 16),
                _pixel_center_bounds(x_coordinates, 5, 18),
            )
        ),
        context={'time': 2.0, 'echo_time': 9.1, 'flip_angle': 15.0},
    )

    with pytest.raises(
        ValueError,
        match=r"value 9\.1 is not present on axis 'echo_time'",
    ):
        mean_over_roi(data_object, roi, 'magnitude')


def test_mean_over_roi_requires_plane_within_tolerance() -> None:
    data_object = synthetic_mri()
    z_coordinates = _synthetic_world_coordinates(data_object, 'z')
    y_coordinates = _synthetic_world_coordinates(data_object, 'y')
    x_coordinates = _synthetic_world_coordinates(data_object, 'x')
    polygon = Polygon(
        _rectangle(
            _pixel_center_bounds(y_coordinates, 6, 16),
            _pixel_center_bounds(x_coordinates, 5, 18),
        )
    )
    spacing = z_coordinates[9] - z_coordinates[8]
    tolerance = 0.25 * spacing
    inside = _make_synthetic_roi(
        data_object,
        polygon,
        plane_value=float(z_coordinates[8] + 0.24 * spacing),
    )
    outside = _make_synthetic_roi(
        data_object,
        polygon,
        plane_value=float(z_coordinates[8] + 0.26 * spacing),
    )

    assert () in mean_over_roi(
        data_object, inside, 'magnitude', tolerance=tolerance
    )
    with pytest.raises(
        ValueError,
        match='ROI plane does not match a target slice within tolerance',
    ):
        mean_over_roi(data_object, outside, 'magnitude', tolerance=tolerance)


def test_mean_over_roi_reports_representative_errors() -> None:
    data_object = synthetic_mri()
    y_coordinates = _synthetic_world_coordinates(data_object, 'y')
    x_coordinates = _synthetic_world_coordinates(data_object, 'x')
    polygon = Polygon(
        _rectangle(
            _pixel_center_bounds(y_coordinates, 6, 16),
            _pixel_center_bounds(x_coordinates, 5, 18),
        )
    )
    roi = _make_synthetic_roi(data_object, polygon)

    with pytest.raises(KeyError, match="unknown field 'missing'"):
        mean_over_roi(data_object, roi, 'missing')
    with pytest.raises(
        ValueError, match='sample_axes must name non-spatial domain axes'
    ):
        mean_over_roi(data_object, roi, 'magnitude', sample_axes=('x',))
    with pytest.raises(
        TypeError, match='tolerance must be a real number or None'
    ):
        mean_over_roi(data_object, roi, 'magnitude', tolerance='nearby')
    with pytest.raises(ValueError, match='tolerance must be finite'):
        mean_over_roi(data_object, roi, 'magnitude', tolerance=np.nan)
    with pytest.raises(ValueError, match='tolerance must be non-negative'):
        mean_over_roi(data_object, roi, 'magnitude', tolerance=-1.0)

    other_frame = CoordinateFrame(
        'other scanner',
        tuple(
            (axis.name, axis.unit)
            for axis in data_object.embeddings[0].target_frame.axes
        ),
    )
    other_frame_roi = _make_synthetic_roi(
        data_object, polygon, frame=other_frame
    )
    with pytest.raises(
        ValueError,
        match='exactly one embedding into the anchor frame',
    ):
        mean_over_roi(data_object, other_frame_roi, 'magnitude')

    outside_polygon = Polygon(
        np.array(
            [
                [1000.0, 1000.0],
                [1001.0, 1000.0],
                [1001.0, 1001.0],
                [1000.0, 1001.0],
            ]
        )
    )
    empty_roi = _make_synthetic_roi(data_object, outside_polygon)
    with pytest.raises(
        ValueError, match='ROI contains no in-plane pixel centers'
    ):
        mean_over_roi(data_object, empty_roi, 'magnitude')

    non_grid_target = DataObject(
        'non-grid target',
        PolygonSetDomain((polygon,), ('y', 'x'), ('mm', 'mm')),
    )
    non_grid_geometry = DataObject(
        'non-grid ROI geometry',
        PolygonSetDomain((polygon,), ('y', 'x'), ('mm', 'mm')),
    )
    non_grid_roi = ROI(
        'non-grid ROI',
        non_grid_geometry,
        PlaneAnchor(
            data_object.embeddings[0].target_frame,
            'z',
            0.0,
            ('y', 'x'),
        ),
        non_grid_target,
    )
    with pytest.raises(
        TypeError, match='mean_over_roi requires a StructuredGridDomain'
    ):
        mean_over_roi(non_grid_target, non_grid_roi, 'magnitude')


def test_measurement_excludes_declared_missing_values_and_rejects_stale_anchor():
    from napari.experimental._data_model import (
        ArraySource,
        CoordinateEmbedding,
        Field,
        StructuredGridDomain,
    )

    obj = synthetic_mri()
    y = _synthetic_world_coordinates(obj, 'y')
    x = _synthetic_world_coordinates(obj, 'x')
    roi = _make_synthetic_roi(
        obj,
        Polygon(
            _rectangle(
                _pixel_center_bounds(y, 6, 7), _pixel_center_bounds(x, 6, 7)
            )
        ),
    )
    values = np.full(obj.domain.shape, 2.0, dtype=np.float32)
    values[1, 2, 1, 8, 6, 6] = -1
    source = ArraySource(values)
    obj.update(fields={'signal': Field('signal', source, missing_value=-1)})
    assert mean_over_roi(obj, roi, 'signal')[()] == pytest.approx(2.0)
    values.fill(-1)
    source.invalidate()
    with pytest.raises(ValueError, match='no valid samples'):
        mean_over_roi(obj, roi, 'signal')
    values.fill(2)
    values[1, 2, 1, 8, 6, 6] = np.nan
    source.invalidate()
    obj.update(
        fields={
            'signal': Field('signal', source, missing_value=np.array(np.nan))
        }
    )
    assert mean_over_roi(obj, roi, 'signal')[()] == pytest.approx(2.0)
    old = obj.domain
    new = StructuredGridDomain(old.axes)
    embedding = obj.embeddings[0]
    obj.update(
        domain=new,
        embeddings=[
            CoordinateEmbedding(
                new, embedding.target_frame, embedding.matrix, embedding.offset
            )
        ],
    )
    assert not roi.is_valid
    with pytest.raises(ValueError, match='target domain changed'):
        mean_over_roi(obj, roi, 'signal')


def test_measurement_rejects_change_after_source_read():
    from napari.experimental._data_model import (
        ArraySource,
        Field,
        SourceChangedError,
    )

    class ChangingSource(ArraySource):
        def read(self, region, *, level=0):
            result = super().read(region, level=level)
            self.invalidate()
            return result

    obj = synthetic_mri()
    y = _synthetic_world_coordinates(obj, 'y')
    x = _synthetic_world_coordinates(obj, 'x')
    roi = _make_synthetic_roi(
        obj,
        Polygon(
            _rectangle(
                _pixel_center_bounds(y, 6, 7), _pixel_center_bounds(x, 6, 7)
            )
        ),
    )
    obj.update(
        fields={
            'signal': Field(
                'signal', ChangingSource(np.ones(obj.domain.shape))
            )
        }
    )
    with pytest.raises(SourceChangedError, match='during ROI measurement'):
        mean_over_roi(obj, roi, 'signal')
