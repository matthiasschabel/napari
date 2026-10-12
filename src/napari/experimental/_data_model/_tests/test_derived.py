from __future__ import annotations

import numpy as np
import pytest

from napari.experimental._data_model import DerivedSource


class CountingSource:
    def __init__(self, data: np.ndarray, *, levels: int = 1) -> None:
        self.shape = data.shape
        self.dtype = data.dtype
        self.levels = levels
        self._data = data
        self.reads: list[tuple[tuple[slice, ...], int]] = []

    def level_shape(self, level: int) -> tuple[int, ...]:
        return self.shape

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        self.reads.append((region, level))
        return self._data[region]


def test_derived_source_rejects_non_data_source() -> None:
    with pytest.raises(TypeError, match='must wrap a DataSource'):
        DerivedSource(object(), np.abs)  # type: ignore[arg-type]


def test_derived_source_rejects_non_callable_operation() -> None:
    source = CountingSource(np.ones(2))

    with pytest.raises(TypeError, match='operation must be callable'):
        DerivedSource(source, object())  # type: ignore[arg-type]


def test_derived_source_rejects_operation_that_cannot_probe_empty_array() -> (
    None
):
    def reject_empty(data: np.ndarray) -> np.ndarray:
        if data.size == 0:
            raise ValueError('empty input is unsupported')
        return data

    with pytest.raises(TypeError, match='must accept an empty array'):
        DerivedSource(CountingSource(np.ones(2)), reject_empty)


def test_derived_source_is_lazy_and_preserves_source_geometry() -> None:
    data = (np.arange(12, dtype=np.float32).reshape(3, 4) * (1 + 0.5j)).astype(
        np.complex64
    )
    source = CountingSource(data, levels=2)

    derived = DerivedSource(source, np.abs)

    assert source.reads == []
    assert derived.source is source
    assert derived.shape == source.shape
    assert derived.levels == 2
    assert derived.level_shape(1) == source.shape
    assert derived.level_chunks(1) is None
    assert derived.dtype == np.dtype(np.float32)

    region = (slice(1, 3), slice(2, 4))
    result = derived.read(region, level=1)

    np.testing.assert_allclose(result, np.abs(data[region]))
    assert source.reads == [(region, 1)]


def test_derived_source_angle_dtype_matches_numpy() -> None:
    source = CountingSource(np.ones((2, 3), dtype=np.complex128))

    derived = DerivedSource(source, np.angle)

    assert source.reads == []
    assert derived.dtype == np.angle(np.empty(0, dtype=np.complex128)).dtype


def test_derived_read_rejects_announced_edit_during_operation():
    from napari.experimental._data_model import ArraySource, SourceChangedError

    source = ArraySource(np.array([3 + 4j]))

    def magnitude(values):
        if values.size:
            source.invalidate()
        return np.abs(values)

    derived = DerivedSource(source, magnitude)
    with pytest.raises(SourceChangedError, match='while deriving'):
        derived.read((slice(None),))
