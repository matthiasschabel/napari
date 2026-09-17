from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from napari.experimental._data_model._source import (
    DataSource,
    SourceChangedError,
    source_level_geometry,
    source_revision,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from napari.experimental._data_model._level_geometry import LevelGeometry


class DerivedSource:
    """Apply a pointwise operation lazily to another data source.

    Construction probes the operation with an empty array to determine its
    result dtype. It never reads the wrapped source.

    Parameters
    ----------
    source : DataSource
        Source whose shape and resolution levels are preserved.
    operation : callable
        Pointwise NumPy-style operation applied independently to each region
        read from ``source``.
    """

    __slots__ = ('_dtype', '_operation', '_source')

    def __init__(
        self,
        source: DataSource,
        operation: Callable[[np.ndarray], Any],
    ) -> None:
        if not isinstance(source, DataSource):
            raise TypeError('derived source must wrap a DataSource')
        if not callable(operation):
            raise TypeError('derived source operation must be callable')

        sample = np.empty((0,), dtype=np.dtype(source.dtype))
        try:
            dtype = np.result_type(operation(sample))
        except (TypeError, ValueError) as exc:
            raise TypeError(
                'derived source operation must accept an empty array'
            ) from exc

        self._source = source
        self._operation = operation
        self._dtype = dtype

    @property
    def revision(self) -> int:
        """Revision of the source on which this computation depends."""
        return source_revision(self.source)

    @property
    def source(self) -> DataSource:
        """Wrapped source."""
        return self._source

    @property
    def shape(self) -> tuple[int, ...]:
        """Storage shape shared with the wrapped source."""
        return tuple(int(size) for size in self.source.shape)

    @property
    def dtype(self) -> np.dtype[Any]:
        """Result dtype inferred without reading the wrapped source."""
        return self._dtype

    @property
    def levels(self) -> int:
        """Resolution-level count shared with the wrapped source."""
        return self.source.levels

    def level_geometry(self, level: int) -> LevelGeometry | None:
        return source_level_geometry(self.source, level)

    def level_shape(self, level: int) -> tuple[int, ...]:
        """Storage shape shared with the wrapped source at one level."""
        return tuple(int(size) for size in self.source.level_shape(level))

    def level_chunks(self, level: int) -> tuple[int, ...] | None:
        """Return no chunk metadata for a derived array."""
        self.source.level_shape(level)
        return None

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        """Read one source region and apply the pointwise operation."""
        revision = self.revision
        result = np.asarray(
            self._operation(self.source.read(region, level=level))
        )
        if revision != self.revision:
            raise SourceChangedError('source changed while deriving a region')
        return result
