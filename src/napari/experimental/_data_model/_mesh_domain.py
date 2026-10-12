"""Mesh topology with independent point and cell field associations."""

from __future__ import annotations

from dataclasses import dataclass

from napari.experimental._data_model._axis import CoordinateFrame
from napari.experimental._data_model._source import (
    DataSource,
    _validate_source,
)


@dataclass(frozen=True, slots=True, eq=False)
class MeshDomain:
    """A homogeneous mesh with lazy point positions and cell connectivity.

    ``points`` has shape ``(point, coordinate)`` in ``intrinsic_frame``;
    ``cells`` has shape ``(cell, vertex)`` and stores integer point indices.
    Construction validates headers only. Connectivity bounds, degeneracy and
    geometric validity belong to consuming mesh algorithms. Named dimensions
    ``('point',)`` and ``('cell',)`` bind fields to independent cardinalities;
    the legacy unnamed association is cell. Mixed cell arities are unsupported.
    """

    points: DataSource
    cells: DataSource
    intrinsic_frame: CoordinateFrame

    def __post_init__(self) -> None:
        if not isinstance(self.intrinsic_frame, CoordinateFrame):
            raise TypeError('mesh intrinsic_frame must be a CoordinateFrame')
        point_shape, point_dtype = _validate_source(self.points)
        cell_shape, cell_dtype = _validate_source(self.cells)
        if (
            len(point_shape) != 2
            or point_shape[1] != self.intrinsic_frame.ndim
        ):
            raise ValueError('mesh point shape must match the intrinsic frame')
        if point_dtype.kind not in 'fiu':
            raise TypeError('mesh points require real numeric storage')
        if len(cell_shape) != 2 or cell_shape[1] < 2:
            raise ValueError(
                'mesh cells require at least two vertex indices per cell'
            )
        if cell_dtype.kind not in 'iu':
            raise TypeError('mesh connectivity requires integer storage')
        if self.points.levels != 1 or self.cells.levels != 1:
            raise ValueError('mesh topology requires single-level sources')

    @property
    def shape(self) -> tuple[int, ...]:
        return (self.cells.shape[0],)

    def association_shape(
        self, dimensions: tuple[str, ...]
    ) -> tuple[int, ...]:
        """Resolve a point, cell, or domain-constant association."""
        if dimensions == ():
            return ()
        if dimensions == ('point',):
            return (self.points.shape[0],)
        if dimensions == ('cell',):
            return self.shape
        raise ValueError('mesh dimensions must be point, cell, or empty')
