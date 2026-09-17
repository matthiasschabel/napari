from __future__ import annotations

from itertools import pairwise
from numbers import Integral
from typing import Any, Protocol, runtime_checkable

import numpy as np

from napari.experimental._data_model._level_geometry import LevelGeometry

_RegionBounds = tuple[tuple[int, int, int], ...]


@runtime_checkable
class DataSource(Protocol):
    """Protocol for region-based field data access.

    Regions passed to :meth:`read` are expressed in the selected level's own
    index space. Level zero has :attr:`shape`; :meth:`level_shape` reports
    array extents, not physical placement. The optional level_geometry method
    supplies a declared mapping to base indices through source_level_geometry.
    """

    @property
    def shape(self) -> tuple[int, ...]:
        """Storage shape at the default resolution level."""
        ...

    @property
    def dtype(self) -> np.dtype[Any]:
        """Storage dtype."""
        ...

    @property
    def levels(self) -> int:
        """Number of available multiscale levels."""
        ...

    def level_shape(self, level: int) -> tuple[int, ...]:
        """Storage shape at one resolution level."""
        ...

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        """Materialize exact data in one level's own index space.

        Raises SourceChangedError if an announced edit invalidates the read.
        Scientific callers choose whether to retry the complete operation;
        retrying individual regions could mix revisions in one calculation.
        """
        ...


class SourceChangedError(RuntimeError):
    """An announced source change invalidated an in-flight read."""


def source_revision(source: DataSource) -> int:
    """Return an optional monotonic source revision without reading payloads.

    Unversioned sources are treated as unchanged. Their owner must explicitly
    invalidate each cache after external edits. Revisions detect announced
    changes; owners must serialize borrowed-buffer writes against reads.
    """
    revision = getattr(source, 'revision', 0)
    if isinstance(revision, bool) or not isinstance(revision, Integral):
        raise TypeError('source revision must be a non-negative integer')
    if revision < 0:
        raise ValueError('source revision must be a non-negative integer')
    return int(revision)


def _validate_source(source: DataSource) -> tuple[tuple[int, ...], np.dtype]:
    """Validate a source header at a model boundary without reading samples."""
    if not isinstance(source, DataSource):
        raise TypeError('source does not satisfy DataSource')
    if not callable(source.read):
        raise TypeError('source read attribute must be callable')
    if not callable(source.level_shape):
        raise TypeError('source level_shape attribute must be callable')
    try:
        shape = tuple(int(size) for size in source.shape)
        dtype = np.dtype(source.dtype)
    except (TypeError, ValueError) as exc:
        raise TypeError('source has an invalid shape or dtype') from exc
    if (
        isinstance(source.levels, bool)
        or not isinstance(source.levels, int)
        or source.levels < 1
    ):
        raise ValueError('source levels must be a positive integer')
    try:
        level_zero_shape = tuple(int(size) for size in source.level_shape(0))
    except (TypeError, ValueError) as exc:
        raise TypeError('source has an invalid level-zero shape') from exc
    if level_zero_shape != shape:
        raise ValueError('source level-zero shape must equal shape')

    return shape, dtype


class ArraySource:
    """Adapt an indexable array-like object to :class:`DataSource`.

    Construction inspects only ``shape`` and ``dtype`` when the input exposes
    them, so lazy arrays such as Dask arrays are not materialized.
    """

    __slots__ = ('_array', '_dtype', '_revision', '_shape')

    def __init__(self, array: Any) -> None:
        if hasattr(array, 'shape') and hasattr(array, 'dtype'):
            storage = array
        else:
            storage = np.asarray(array)
        if not hasattr(storage, '__getitem__'):
            raise TypeError('array source must support region indexing')

        try:
            shape = tuple(int(size) for size in storage.shape)
            dtype = np.dtype(storage.dtype)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                'array source must expose a valid shape and dtype'
            ) from exc
        self._revision = 0
        self._array = storage
        self._shape = shape
        self._dtype = dtype

    @property
    def revision(self) -> int:
        """Monotonic revision of explicitly announced storage edits."""
        return self._revision

    def invalidate(self) -> None:
        """Announce external value edits; shape and dtype must stay fixed.

        Serialize edits and this notification against reads. For a pyramid,
        update every affected level before announcing the change.
        """
        self._revision += 1

    @property
    def shape(self) -> tuple[int, ...]:
        """Storage shape."""
        return self._shape

    @property
    def dtype(self) -> np.dtype[Any]:
        """Storage dtype."""
        return self._dtype

    @property
    def levels(self) -> int:
        """Number of multiscale levels, currently one."""
        return 1

    def level_shape(self, level: int) -> tuple[int, ...]:
        """Return the level-zero storage shape."""
        _validate_level(level, self.levels)
        return self.shape

    def level_chunks(self, level: int) -> tuple[int, ...] | None:
        """Return no chunk metadata for an adapted single-scale array."""
        _validate_level(level, self.levels)
        return None

    @property
    def writable(self) -> bool:
        """Whether legacy NumPy editing can safely target this storage.

        Lazy/indexable storage does not imply an assignment capability.
        """
        return (
            isinstance(self._array, np.ndarray) and self._array.flags.writeable
        )

    @property
    def array(self) -> Any:
        """Wrapped array-like object without materializing it."""
        return self._array

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        """Materialize a requested region.

        Parameters
        ----------
        region : tuple of slice
            One slice for every storage dimension.
        level : int
            Resolution level. Array-backed sources expose only level zero.
        """
        _validate_level(level, self.levels)
        _validate_region(region, self.level_shape(level))
        revision = self.revision
        result = np.asarray(self._array[region])
        if revision != self.revision:
            raise SourceChangedError('array changed while reading a region')
        return result


class MultiscaleSource:
    """Adapt an ordered pyramid of indexable array-like objects.

    Each level is indexed directly and lazily. Regions passed to :meth:`read`
    therefore use that level's own index space rather than level-zero index
    coordinates.

    Parameters
    ----------
    arrays : iterable of array-like
        Finest-to-coarsest levels. Every level must expose the same dtype and
        dimensionality, and shapes must be monotonically non-increasing along
        every axis.
    level_geometries : tuple of LevelGeometry or None
        Optional declared level-to-base index transforms. The first must be
        identity. Missing declarations remain unknown; shape ratios are not
        substituted as physical geometry.
    """

    __slots__ = (
        '_arrays',
        '_dtype',
        '_level_geometries',
        '_revision',
        '_shapes',
    )

    def __init__(
        self,
        arrays: Any,
        *,
        level_geometries: tuple[LevelGeometry, ...] | None = None,
    ) -> None:
        try:
            levels = tuple(arrays)
        except TypeError as exc:
            raise TypeError(
                'multiscale source levels must be iterable'
            ) from exc
        if not levels:
            raise ValueError('multiscale source requires at least one level')

        validated: list[Any] = []
        shapes: list[tuple[int, ...]] = []
        dtypes: list[np.dtype[Any]] = []
        for level in levels:
            if not (
                hasattr(level, 'shape')
                and hasattr(level, 'dtype')
                and hasattr(level, '__getitem__')
            ):
                raise TypeError(
                    'multiscale levels must expose shape, dtype, and indexing'
                )
            try:
                shape = tuple(int(size) for size in level.shape)
                dtype = np.dtype(level.dtype)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    'multiscale levels must expose valid shapes and dtypes'
                ) from exc
            if any(size < 0 for size in shape):
                raise ValueError(
                    'multiscale level shapes must be non-negative'
                )
            validated.append(level)
            shapes.append(shape)
            dtypes.append(dtype)

        ndim = len(shapes[0])
        if any(len(shape) != ndim for shape in shapes[1:]):
            raise ValueError(
                'multiscale levels must have consistent dimensions'
            )
        if any(dtype != dtypes[0] for dtype in dtypes[1:]):
            raise TypeError('multiscale levels must have a consistent dtype')
        for finer, coarser in pairwise(shapes):
            if any(
                coarse > fine
                for fine, coarse in zip(finer, coarser, strict=True)
            ):
                raise ValueError(
                    'multiscale level shapes must be monotonically non-increasing'
                )

        self._revision = 0
        if level_geometries is None:
            self._level_geometries = None
        else:
            geometries = tuple(level_geometries)
            if len(geometries) != len(shapes):
                raise ValueError(
                    'level geometry count must match the stored levels'
                )
            if any(
                not isinstance(geometry, LevelGeometry)
                for geometry in geometries
            ):
                raise TypeError(
                    'level geometries must be LevelGeometry objects'
                )
            if any(len(geometry.scale) != ndim for geometry in geometries):
                raise ValueError('level geometry rank must match storage rank')
            if not geometries[0].is_identity():
                raise ValueError('level zero geometry must be identity')
            if any(
                any(
                    coarse < fine
                    for fine, coarse in zip(
                        finer.scale, coarser.scale, strict=True
                    )
                )
                for finer, coarser in pairwise(geometries)
            ):
                raise ValueError(
                    'declared level scales must be monotonically non-decreasing'
                )
            self._level_geometries = geometries
        self._arrays = tuple(validated)
        self._shapes = tuple(shapes)
        self._dtype = dtypes[0]

    @property
    def revision(self) -> int:
        """Monotonic revision of explicitly announced storage edits."""
        return self._revision

    def invalidate(self) -> None:
        """Announce external value edits; shape and dtype must stay fixed.

        Serialize edits and this notification against reads. For a pyramid,
        update every affected level before announcing the change.
        """
        self._revision += 1

    @property
    def shape(self) -> tuple[int, ...]:
        """Finest level's storage shape."""
        return self._shapes[0]

    @property
    def dtype(self) -> np.dtype[Any]:
        """Storage dtype shared by every level."""
        return self._dtype

    @property
    def levels(self) -> int:
        """Number of pyramid levels."""
        return len(self._arrays)

    @property
    def arrays(self) -> tuple[Any, ...]:
        """Wrapped levels without materializing them."""
        return self._arrays

    def level_geometry(self, level: int) -> LevelGeometry | None:
        """Declared level index mapping to base, or None when unknown."""
        _validate_level(level, self.levels)
        return (
            None
            if self._level_geometries is None
            else self._level_geometries[level]
        )

    def level_shape(self, level: int) -> tuple[int, ...]:
        """Return one level's storage shape."""
        _validate_level(level, self.levels)
        return self._shapes[level]

    def level_chunks(self, level: int) -> tuple[int, ...] | None:
        """Return regular chunk sizes exposed by one level, when available."""
        _validate_level(level, self.levels)
        chunks = getattr(self._arrays[level], 'chunks', None)
        if chunks is None:
            return None
        try:
            normalized = tuple(int(size) for size in chunks)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                'multiscale level chunks must contain integer sizes'
            ) from exc
        if len(normalized) != len(self._shapes[level]):
            raise ValueError(
                'multiscale level chunk dimensionality must match its shape'
            )
        return normalized

    def level_shards(self, level: int) -> tuple[int, ...] | None:
        """Return the write-shard sizes one level exposes, when available.

        A shard is the unit a store transfers; it equals the chunk grid
        for an unsharded array, in which case the level advertises no
        shards at all.
        """
        _validate_level(level, self.levels)
        shards = getattr(self._arrays[level], 'shards', None)
        if shards is None:
            return None
        try:
            normalized = tuple(int(size) for size in shards)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                'multiscale level shards must contain integer sizes'
            ) from exc
        if len(normalized) != len(self._shapes[level]):
            raise ValueError(
                'multiscale level shard dimensionality must match its shape'
            )
        return normalized

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        """Materialize a region expressed in the selected level's index space."""
        _validate_level(level, self.levels)
        _validate_region(region, self.level_shape(level))
        revision = self.revision
        result = np.asarray(self._arrays[level][region])
        if revision != self.revision:
            raise SourceChangedError('pyramid changed while reading a region')
        return result


def source_level_geometry(
    source: DataSource, level: int
) -> LevelGeometry | None:
    """Return declared level-to-base sample geometry, never infer it from shapes.

    Level zero has identity geometry. Other levels without this optional
    capability return None. Consumers that require physical placement must
    reject an unknown mapping or explicitly own a display-only convention.
    """
    _validate_level(level, source.levels)
    resolve = getattr(source, 'level_geometry', None)
    if resolve is not None and not callable(resolve):
        raise TypeError('source level_geometry must be callable')
    geometry = resolve(level) if resolve is not None else None
    if geometry is None:
        return (
            LevelGeometry((1.0,) * len(source.shape)) if level == 0 else None
        )
    if not isinstance(geometry, LevelGeometry):
        raise TypeError(
            'source level_geometry must return LevelGeometry or None'
        )
    if len(geometry.scale) != len(source.shape):
        raise ValueError('source level geometry rank must match storage rank')
    if level == 0 and not geometry.is_identity():
        raise ValueError('level zero geometry must be identity')
    return geometry


def _display_level_geometry(source: DataSource, level: int) -> LevelGeometry:
    """Declared geometry, or the legacy shape-ratio display convention."""
    geometry = source_level_geometry(source, level)
    if geometry is not None:
        return geometry
    return LevelGeometry(
        tuple(
            base / size if size else 1.0
            for base, size in zip(
                source.shape, source.level_shape(level), strict=True
            )
        )
    )


def source_level_chunks(source: Any, level: int) -> tuple[int, ...] | None:
    """Return one level's regular chunk sizes when the source exposes them.

    ``None`` when the source advertises no chunk metadata at all or none
    for this level, which callers read as "this level is one block".
    """
    level_chunks = getattr(source, 'level_chunks', None)
    if not callable(level_chunks):
        return None
    chunks = level_chunks(level)
    return None if chunks is None else tuple(int(size) for size in chunks)


def source_level_shards(source: Any, level: int) -> tuple[int, ...] | None:
    """Return one level's shard sizes when the source exposes them.

    ``None`` when the source advertises no shard metadata, which callers
    read as "a chunk is the transfer unit". A shard is what a store
    actually fetches, so a transfer-cost estimate counts shard bytes
    rather than chunk bytes on a sharded array.
    """
    level_shards = getattr(source, 'level_shards', None)
    if not callable(level_shards):
        return None
    shards = level_shards(level)
    return None if shards is None else tuple(int(size) for size in shards)


def _normalize_region(
    region: tuple[slice, ...], shape: tuple[int, ...]
) -> tuple[slice, ...]:
    if not isinstance(region, tuple) or not all(
        isinstance(item, slice) for item in region
    ):
        raise TypeError('region must be a tuple of slices')
    if len(region) != len(shape):
        raise ValueError('region dimensionality must match source shape')

    normalized: list[slice] = []
    for item, size in zip(region, shape, strict=True):
        try:
            start, stop, step = item.indices(size)
        except TypeError as exc:
            raise TypeError(
                'region slice bounds and steps must be integers or None'
            ) from exc
        if step < 0:
            raise ValueError('region slices do not support negative steps')
        normalized.append(slice(start, stop, step))
    return tuple(normalized)


def _validate_level(level: int, levels: int) -> None:
    if isinstance(level, bool) or not isinstance(level, int):
        raise TypeError('resolution level must be an integer')
    if level < 0 or level >= levels:
        raise IndexError(
            f'resolution level {level} is outside the available levels'
        )


def _validate_region(
    region: tuple[slice, ...], shape: tuple[int, ...]
) -> None:
    if not isinstance(region, tuple) or not all(
        isinstance(item, slice) for item in region
    ):
        raise TypeError('region must be a tuple of slices')
    if len(region) != len(shape):
        raise ValueError('region dimensionality must match source shape')
    for axis, (item, size) in enumerate(zip(region, shape, strict=True)):
        if item.step is not None and item.step < 0:
            raise ValueError('region slices do not support negative steps')
        for bound_name, bound in (('start', item.start), ('stop', item.stop)):
            if bound is not None and (bound < 0 or bound > size):
                raise ValueError(
                    f'region slice {bound_name} {bound} is outside axis '
                    f'{axis} with size {size}'
                )
