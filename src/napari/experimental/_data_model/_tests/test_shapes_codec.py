from __future__ import annotations

import numpy as np
import pytest

from napari.experimental._data_model import (
    Polygon,
    polygon_to_shape_vertices,
    shape_vertices_to_polygon,
)
from napari.layers import Shapes


def _rectangle(
    x_min: float, y_min: float, x_max: float, y_max: float
) -> np.ndarray:
    return np.array(
        [
            [x_min, y_min],
            [x_min, y_max],
            [x_max, y_max],
            [x_max, y_min],
        ],
        dtype=float,
    )


def _polygon_with_holes(count: int) -> Polygon:
    exterior = np.array([[0.0, 0.0], [20.0, 0.0], [20.0, 20.0], [0.0, 20.0]])
    holes = (
        _rectangle(2.0, 2.0, 4.0, 4.0),
        _rectangle(8.0, 8.0, 11.0, 11.0),
        _rectangle(14.0, 3.0, 18.0, 6.0),
    )
    return Polygon(exterior, holes[:count])


@pytest.mark.parametrize('hole_count', [0, 1, 2, 3])
def test_shapes_codec_round_trip(hole_count: int) -> None:
    polygon = _polygon_with_holes(hole_count)

    vertices = polygon_to_shape_vertices(polygon)
    restored = shape_vertices_to_polygon(vertices)

    expected_size = len(polygon.exterior)
    if polygon.holes:
        expected_size += sum(len(hole) + 2 for hole in polygon.holes)
    assert vertices.shape == (expected_size, 2)
    np.testing.assert_array_equal(restored.exterior, polygon.exterior)
    assert len(restored.holes) == hole_count
    for restored_hole, expected_hole in zip(
        restored.holes, polygon.holes, strict=True
    ):
        np.testing.assert_array_equal(restored_hole, expected_hole)


def test_shapes_codec_returns_to_every_ring_anchor() -> None:
    polygon = _polygon_with_holes(3)
    vertices = polygon_to_shape_vertices(polygon)
    cursor = len(polygon.exterior)

    for hole in polygon.holes:
        np.testing.assert_array_equal(
            vertices[cursor : cursor + len(hole)], hole
        )
        cursor += len(hole)
        np.testing.assert_array_equal(vertices[cursor], hole[0])
        np.testing.assert_array_equal(
            vertices[cursor + 1], polygon.exterior[-1]
        )
        cursor += 2
    assert cursor == len(vertices)


def test_shapes_codec_round_trip_is_exact_for_same_input_winding() -> None:
    exterior = _rectangle(0.0, 0.0, 20.0, 20.0)[::-1]
    hole = _rectangle(3.0, 3.0, 7.0, 7.0)[::-1]
    polygon = Polygon(exterior, (hole,))

    restored = shape_vertices_to_polygon(polygon_to_shape_vertices(polygon))

    np.testing.assert_array_equal(restored.exterior, polygon.exterior)
    np.testing.assert_array_equal(restored.holes[0], polygon.holes[0])


def test_shapes_codec_rejects_an_unclosed_encoded_hole() -> None:
    vertices = polygon_to_shape_vertices(_polygon_with_holes(1))[:-1]

    with pytest.raises(ValueError, match='return to its own anchor'):
        shape_vertices_to_polygon(vertices)


def _mesh_contains_point(
    vertices: np.ndarray, triangles: np.ndarray, point: np.ndarray
) -> bool:
    triangle_vertices = vertices[triangles]
    first = triangle_vertices[:, 0]
    second = triangle_vertices[:, 1]
    third = triangle_vertices[:, 2]

    def cross(left: np.ndarray, right: np.ndarray) -> np.ndarray:
        return left[:, 0] * right[:, 1] - left[:, 1] * right[:, 0]

    first_cross = cross(second - first, point - first)
    second_cross = cross(third - second, point - second)
    third_cross = cross(first - third, point - third)
    has_negative = (first_cross < 0) | (second_cross < 0) | (third_cross < 0)
    has_positive = (first_cross > 0) | (second_cross > 0) | (third_cross > 0)
    return bool(np.any(~(has_negative & has_positive)))


def test_three_hole_encoding_has_the_expected_shapes_mesh() -> None:
    polygon = _polygon_with_holes(3)
    vertices = polygon_to_shape_vertices(polygon)

    layer = Shapes(vertices, shape_type='polygon')
    shape = layer._data_view.shapes[0]
    face_vertices = shape._face_vertices
    face_triangles = shape._face_triangles
    triangle_vertices = face_vertices[face_triangles]
    first_edges = triangle_vertices[:, 1] - triangle_vertices[:, 0]
    second_edges = triangle_vertices[:, 2] - triangle_vertices[:, 0]
    cross_products = (
        first_edges[:, 0] * second_edges[:, 1]
        - first_edges[:, 1] * second_edges[:, 0]
    )
    mesh_area = 0.5 * float(np.sum(np.abs(cross_products)))

    assert mesh_area == pytest.approx(polygon.area, rel=1e-6, abs=0.0)
    assert _mesh_contains_point(
        face_vertices, face_triangles, np.array([6.0, 6.0])
    )
    assert not _mesh_contains_point(
        face_vertices, face_triangles, np.array([3.0, 3.0])
    )
    np.testing.assert_array_equal(
        polygon.contains_points([[6.0, 6.0], [3.0, 3.0]]),
        [True, False],
    )
