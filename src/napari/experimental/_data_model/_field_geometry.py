"""Value declarations shared by scientific fields and acquisition variables."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from typing import Literal

import numpy as np

from napari.experimental._data_model._axis import CoordinateFrame
from napari.experimental._data_model._validation import validate_name


@dataclass(frozen=True, slots=True)
class FieldGeometry:
    """Declare value meaning without inferring it from storage shape.

    ``components`` orders named value components. For physical values it
    orders the basis directions; an omitted order uses ``basis_frame`` axes.
    ``packing`` explicitly lists the matrix-index pairs of a packed symmetric
    tensor, in storage order. Without packing, tensors use a full square array.
    ``basis='orthonormal'`` declares a capability for passive basis conversion;
    a frame reference alone does not make that declaration.
    """

    kind: Literal[
        'scalar',
        'complex',
        'categorical',
        'components',
        'vector',
        'symmetric_tensor',
    ]
    components: tuple[str, ...] = ()
    basis_frame: CoordinateFrame | None = None
    basis: Literal['orthonormal'] | None = None
    packing: tuple[tuple[int, int], ...] | None = None

    def __post_init__(self) -> None:
        if self.kind not in (
            'scalar',
            'complex',
            'categorical',
            'components',
            'vector',
            'symmetric_tensor',
        ):
            raise ValueError('unknown field value kind')
        if self.basis_frame is not None and not isinstance(
            self.basis_frame, CoordinateFrame
        ):
            raise TypeError('value basis_frame must be a CoordinateFrame')
        if self.basis not in (None, 'orthonormal'):
            raise ValueError('unsupported value basis declaration')
        if self.basis is not None and self.basis_frame is None:
            raise ValueError(
                'a value basis declaration requires a basis frame'
            )
        if isinstance(self.components, str):
            raise TypeError('value components must be a sequence of names')
        components = tuple(self.components)
        if not components and self.basis_frame is not None:
            components = tuple(axis.name for axis in self.basis_frame.axes)
        for name in components:
            validate_name(name, kind='value component')
        if len(components) != len(set(components)):
            raise ValueError('value component names must be unique')
        physical = self.kind in ('vector', 'symmetric_tensor')
        if self.basis_frame is not None:
            axes = tuple(axis.name for axis in self.basis_frame.axes)
            if not physical or set(components) != set(axes):
                raise ValueError(
                    'physical component names must match the basis frame'
                )
        if self.kind in ('scalar', 'complex', 'categorical') and components:
            raise ValueError('scalar values cannot declare component axes')
        if (
            self.kind in ('components', 'vector', 'symmetric_tensor')
            and not components
        ):
            raise ValueError(
                'component values require declared component names'
            )
        object.__setattr__(self, 'components', components)
        if self.packing is not None:
            if self.kind != 'symmetric_tensor':
                raise ValueError('packing requires a symmetric tensor')
            packing = tuple(tuple(pair) for pair in self.packing)
            if any(
                len(pair) != 2
                or any(
                    isinstance(i, bool) or not isinstance(i, Integral)
                    for i in pair
                )
                for pair in packing
            ):
                raise TypeError(
                    'tensor packing must contain integer index pairs'
                )
            pairs = {tuple(sorted(pair)) for pair in packing}
            expected = {
                (i, j)
                for i in range(len(components))
                for j in range(i, len(components))
            }
            if len(packing) != len(expected) or pairs != expected:
                raise ValueError(
                    'tensor packing must cover each symmetric component once'
                )
            object.__setattr__(self, 'packing', packing)

    def validate_storage(
        self, component_shape: tuple[int, ...], dtype: np.dtype
    ) -> None:
        """Validate declared components using headers, without reading values."""
        n = len(self.components)
        if self.kind == 'symmetric_tensor':
            expected = (
                (len(self.packing),) if self.packing is not None else (n, n)
            )
        elif self.kind in ('vector', 'components'):
            expected = (n,)
        else:
            expected = ()
        if component_shape != expected:
            raise ValueError(
                'storage component shape does not match value declaration'
            )
        if self.kind == 'complex' and not np.issubdtype(
            dtype, np.complexfloating
        ):
            raise TypeError('complex values require complex storage')
        if self.kind in ('vector', 'symmetric_tensor') and (
            not np.issubdtype(dtype, np.number)
            or np.issubdtype(dtype, np.complexfloating)
        ):
            raise TypeError('physical values require real numeric storage')
