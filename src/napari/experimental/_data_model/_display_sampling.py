"""Bounded display sampling onto napari's shape-ratio multiscale grids."""

from itertools import pairwise, product

import numpy as np

from napari.experimental._data_model._index_selection import _IndexedSource
from napari.experimental._data_model._level_geometry import LevelGeometry
from napari.experimental._data_model._source import (
    DataSource,
    SourceChangedError,
    _normalize_region,
    source_level_geometry,
    source_revision,
)


class _PreviewLevel:
    def __init__(self, source: DataSource, level: int):
        self.source = source
        self.level = level
        self.shape = source.level_shape(level)
        self.dtype = source.dtype
        self.levels = 1

    @property
    def revision(self):
        return source_revision(self.source)

    def level_shape(self, level):
        if level != 0:
            raise ValueError('display level view has only level zero')
        return self.shape

    def read(self, region, *, level=0):
        self.level_shape(level)
        preview = getattr(self.source, 'read_preview', None)
        if callable(preview):
            return preview(region, level=self.level)[0]
        return self.source.read(region, level=self.level)


def _read_display_grid(
    source: DataSource, region: tuple[slice, ...], level: int
) -> np.ndarray:
    """Nearest display samples; uncovered coarse support falls back to base.

    This is a presentation boundary, not a scientific DataSource. It keeps
    ordinary Image's 2D tile2data convention: sample j sits at base index
    j * (base_size / level_size). The ordinary 3D volume node adds a further
    (factor - 1) / 2 translation, which this adapter does not compensate.
    """
    shape = source.level_shape(level)
    geometry = source_level_geometry(source, level)
    display = LevelGeometry(
        tuple(
            base / size if size else 1.0
            for base, size in zip(source.shape, shape, strict=True)
        )
    )
    if geometry is None or geometry.matches(display):
        return _PreviewLevel(source, level).read(region)
    revision = source_revision(source)
    normalized = _normalize_region(region, shape)
    base_indices = tuple(
        np.arange(item.start, item.stop, item.step) * scale
        for item, scale in zip(normalized, display.scale, strict=True)
    )
    mapped = tuple(
        np.floor((indices - offset) / scale + 0.5).astype(np.intp)
        for indices, scale, offset in zip(
            base_indices, geometry.scale, geometry.offset, strict=True
        )
    )
    result = np.empty(
        tuple(len(indices) for indices in mapped), dtype=source.dtype
    )
    if not result.size:
        return result
    runs = []
    for indices, size in zip(mapped, shape, strict=True):
        valid = (indices >= 0) & (indices < size)
        boundaries = (
            0,
            *(
                int(index) + 1
                for index in np.flatnonzero(valid[1:] != valid[:-1])
            ),
            len(indices),
        )
        runs.append(
            tuple(
                (slice(start, stop), bool(valid[start]))
                for start, stop in pairwise(boundaries)
            )
        )
    for combination in product(*runs):
        window = tuple(item for item, _ in combination)
        covered = all(valid for _, valid in combination)
        selected = (
            tuple(
                indices[item]
                for indices, item in zip(mapped, window, strict=True)
            )
            if covered
            else tuple(
                np.floor(indices[item] + 0.5).astype(np.intp)
                for indices, item in zip(base_indices, window, strict=True)
            )
        )
        indexed = _IndexedSource(
            _PreviewLevel(source, level if covered else 0), selected, ()
        )
        result[window] = indexed.read((slice(None),) * len(shape))
    if revision != source_revision(source):
        raise SourceChangedError('source changed during display sampling')
    return result
