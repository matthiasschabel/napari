"""Helpers shared by the MPR test modules."""

from __future__ import annotations

from time import monotonic, sleep
from typing import Any

import numpy as np

from napari.experimental._data_model import (
    CoordinateAxis,
    CoordinateEmbedding,
    CoordinateFrame,
    DataObject,
    Field,
    StructuredGridDomain,
)


class CountingSource:
    def __init__(self, data: np.ndarray) -> None:
        self.shape = data.shape
        self.dtype = data.dtype
        self.levels = 1
        self._data = data
        self.regions: list[tuple[slice, ...]] = []

    def level_shape(self, level: int) -> tuple[int, ...]:
        return self.shape

    def read(self, region: tuple[slice, ...], *, level: int = 0) -> np.ndarray:
        self.regions.append(region)
        return self._data[region]


def _three_dimensional_data_object(source: Any) -> DataObject:
    domain = StructuredGridDomain(
        tuple(
            CoordinateAxis(name, size)
            for name, size in zip(('z', 'y', 'x'), source.shape, strict=True)
        )
    )
    target = CoordinateFrame(
        'microscope', (('z', None), ('y', None), ('x', None))
    )
    return DataObject(
        'volume',
        domain,
        {'magnitude': Field('magnitude', source)},
        embeddings=(CoordinateEmbedding(domain, target, np.eye(3)),),
    )


def _four_dimensional_data_object(source: Any) -> DataObject:
    domain = StructuredGridDomain(
        tuple(
            CoordinateAxis(name, size)
            for name, size in zip(
                ('time', 'z', 'y', 'x'), source.shape, strict=True
            )
        )
    )
    target = CoordinateFrame(
        'microscope', (('z', None), ('y', None), ('x', None))
    )
    matrix = np.zeros((3, 4))
    matrix[:, 1:] = np.eye(3)
    return DataObject(
        'volume',
        domain,
        {'magnitude': Field('magnitude', source)},
        embeddings=(CoordinateEmbedding(domain, target, matrix),),
    )


def _wait_for_positions(
    source: CountingSource, expected: set[int], *, timeout: float = 5.0
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        positions = {
            region[0].start
            for region in source.regions
            if region[1:] == (slice(0, 6, 1), slice(0, 5, 1))
        }
        if expected <= positions:
            return
        sleep(0.01)
    raise AssertionError(
        f'prefetch reads did not complete before timeout: {source.regions!r}'
    )
