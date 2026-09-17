from __future__ import annotations

from typing import Any

import numpy as np

from napari.experimental._data_model._geometry import Polygon


def polygon_to_shape_vertices(polygon: Polygon) -> np.ndarray:
    """Encode a Polygon as one napari Shapes polygon vertex sequence.

    A polygon without holes is its exterior ring, still implicitly closed. If
    holes are present, each hole returns to its own first vertex and then to
    the final exterior vertex. These anchor-return pairs form zero-width
    bridges that let napari triangulate several rings as one polygon::

        exterior, hole_0, hole_0[0], exterior[-1], ...,
        hole_n, hole_n[0], exterior[-1]

    The explicit return to the shared exterior anchor after every hole is
    required when encoding three or more rings; omitting intermediate returns
    can produce an incorrect fill. ``Polygon`` supplies the opposite exterior
    and hole winding required by napari's triangulator.
    """
    if not isinstance(polygon, Polygon):
        raise TypeError('polygon must be a Polygon')
    if not polygon.holes:
        return polygon.exterior.copy()

    exterior_anchor = polygon.exterior[-1:]
    encoded_rings = [polygon.exterior]
    for hole in polygon.holes:
        encoded_rings.extend((hole, hole[:1], exterior_anchor))
    return np.concatenate(encoded_rings, axis=0)


def shape_vertices_to_polygon(vertices: Any) -> Polygon:
    """Decode napari Shapes anchor-return vertices into a Polygon.

    For a multi-ring sequence, its final vertex is the shared exterior anchor;
    the first occurrence of that value ends the exterior. Each following hole
    must contain at least three vertices, return to its own first vertex, then
    return to the shared exterior anchor. A sequence with no repeated final
    vertex is a single implicitly closed exterior ring.
    """
    try:
        vertex_array = np.asarray(vertices, dtype=float)
    except (TypeError, ValueError) as exc:
        raise TypeError('vertices must contain numeric values') from exc
    if vertex_array.ndim != 2 or vertex_array.shape[1:] != (2,):
        raise ValueError('vertices must have shape (n, 2)')
    if len(vertex_array) < 3:
        raise ValueError('vertices must contain at least three points')
    if not np.all(np.isfinite(vertex_array)):
        raise ValueError('vertices must be finite')

    exterior_anchor = vertex_array[-1]
    anchor_indices = np.flatnonzero(
        np.all(vertex_array == exterior_anchor, axis=1)
    )
    if len(anchor_indices) == 1:
        return Polygon(vertex_array)

    exterior_end = int(anchor_indices[0])
    if exterior_end < 2:
        raise ValueError(
            'encoded exterior must contain at least three vertices'
        )
    exterior = vertex_array[: exterior_end + 1]
    holes: list[np.ndarray] = []
    cursor = exterior_end + 1
    while cursor < len(vertex_array):
        hole_start = cursor
        hole_end = next(
            (
                index
                for index in range(hole_start + 3, len(vertex_array))
                if np.array_equal(
                    vertex_array[index], vertex_array[hole_start]
                )
            ),
            None,
        )
        if (
            hole_end is None
            or hole_end + 1 >= len(vertex_array)
            or not np.array_equal(vertex_array[hole_end + 1], exterior_anchor)
        ):
            raise ValueError(
                'each encoded hole must return to its own anchor and then '
                'the exterior anchor'
            )
        holes.append(vertex_array[hole_start:hole_end])
        cursor = hole_end + 2
    return Polygon(exterior, holes)
