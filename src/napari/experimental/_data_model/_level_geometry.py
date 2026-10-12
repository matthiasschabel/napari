"""Declared sample positions at one resolution, relative to base indices."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class LevelGeometry:
    """Diagonal level-to-base index mapping, independent of rounded shapes.

    ``base_index = scale * level_index + offset`` applies to each storage
    dimension. Scale is positive; offsets can represent shifted sample centers.
    Component dimensions must retain unit scale and zero offset. Construction
    normalizes small metadata vectors; it never reads sample payloads.
    """

    scale: tuple[float, ...]
    offset: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        scale = tuple(self.scale)
        offset = tuple(self.offset)
        if not offset:
            offset = (0.0,) * len(scale)
        if len(scale) != len(offset):
            raise ValueError('level scale and offset must have equal rank')
        if any(
            isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
            for value in (*scale, *offset)
        ):
            raise TypeError('level geometry requires real numbers')
        if not all(np.isfinite(value) for value in (*scale, *offset)) or any(
            value <= 0 for value in scale
        ):
            raise ValueError(
                'level scale must be positive and geometry finite'
            )
        object.__setattr__(
            self, 'scale', tuple(float(value) for value in scale)
        )
        object.__setattr__(
            self, 'offset', tuple(float(value) for value in offset)
        )

    def is_identity(self, axes: tuple[int, ...] | None = None) -> bool:
        """Whether selected axes retain base indices within roundoff tolerance."""
        axes = tuple(range(len(self.scale))) if axes is None else axes
        return bool(
            np.allclose(
                [self.scale[axis] for axis in axes], 1, rtol=0, atol=1e-12
            )
            and np.allclose(
                [self.offset[axis] for axis in axes], 0, rtol=0, atol=1e-12
            )
        )

    def matches(self, other: LevelGeometry) -> bool:
        """Compare index mappings with absolute 1e-12 metadata tolerance."""
        return (
            isinstance(other, LevelGeometry)
            and len(self.scale) == len(other.scale)
            and bool(
                np.allclose(self.scale, other.scale, rtol=0, atol=1e-12)
                and np.allclose(self.offset, other.offset, rtol=0, atol=1e-12)
            )
        )

    def to_base(self, indices: Any) -> np.ndarray:
        """Map index vectors with final dimension equal to the storage rank."""
        values = np.asarray(indices, dtype=float)
        if values.ndim == 0 or values.shape[-1] != len(self.scale):
            raise ValueError(
                'index vectors must match the level geometry rank'
            )
        return values * self.scale + self.offset
