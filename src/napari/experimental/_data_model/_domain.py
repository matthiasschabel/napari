from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Integral
from typing import Protocol, runtime_checkable

from napari.experimental._data_model._axis import (
    AxisRole,
    CoordinateAxis,
    CoordinateFrame,
    FrameAxis,
)


@runtime_checkable
class Domain(Protocol):
    """Minimal contract shared by structured and geometric domains."""

    @property
    def shape(self) -> tuple[int, ...]:
        """Field shape in domain-element order."""
        ...

    @property
    def intrinsic_frame(self) -> CoordinateFrame:
        """Coordinate frame in which intrinsic geometry is expressed."""
        ...


def validate_domain(domain: object, *, kind: str = 'domain') -> None:
    """Validate the runtime structure promised by :class:`Domain`."""
    if not isinstance(domain, Domain):
        raise TypeError(f'{kind} must satisfy the Domain protocol')
    shape = domain.shape
    if not isinstance(shape, tuple) or any(
        isinstance(size, bool) or not isinstance(size, Integral) or size < 0
        for size in shape
    ):
        raise TypeError(
            f'{kind}.shape must be a tuple of non-negative integers'
        )
    if not isinstance(domain.intrinsic_frame, CoordinateFrame):
        raise TypeError(f'{kind}.intrinsic_frame must be a CoordinateFrame')


@dataclass(frozen=True, slots=True, eq=False)
class StructuredGridDomain:
    """An ordered, named, structured-grid domain.

    Parameters
    ----------
    axes : tuple of CoordinateAxis
        Intrinsic coordinate and local axes in domain order.
    """

    axes: tuple[CoordinateAxis, ...]
    _intrinsic_frame: CoordinateFrame = field(init=False, repr=False)

    def __post_init__(self) -> None:
        axes = tuple(self.axes)
        if not all(isinstance(axis, CoordinateAxis) for axis in axes):
            raise TypeError('domain axes must be CoordinateAxis instances')
        names = [axis.name for axis in axes]
        if len(names) != len(set(names)):
            raise ValueError('domain axis names must be unique')
        if any(axis.role is AxisRole.COMPONENT for axis in axes):
            raise ValueError(
                'a field component axis cannot also be a domain axis'
            )
        object.__setattr__(self, 'axes', axes)
        frame_axes = tuple(FrameAxis(axis.name, axis.unit) for axis in axes)
        object.__setattr__(
            self,
            '_intrinsic_frame',
            CoordinateFrame('intrinsic', frame_axes),
        )

    @property
    def shape(self) -> tuple[int, ...]:
        """Shape of the domain in intrinsic axis order."""
        return tuple(axis.size for axis in self.axes)

    @property
    def intrinsic_frame(self) -> CoordinateFrame:
        """Stable frame representing the domain's intrinsic coordinates."""
        return self._intrinsic_frame

    def association_shape(
        self, dimensions: tuple[str, ...]
    ) -> tuple[int, ...]:
        """Resolve named field dimensions without requiring a full grid field."""
        return tuple(self.axis(name).size for name in dimensions)

    def axis(self, name: str) -> CoordinateAxis:
        """Return an axis by name.

        Parameters
        ----------
        name : str
            Axis name to find.

        Returns
        -------
        CoordinateAxis
            Matching axis descriptor.

        Raises
        ------
        KeyError
            If the name is not present.
        """
        for axis in self.axes:
            if axis.name == name:
                return axis
        raise KeyError(f'unknown domain axis {name!r}')

    def axis_index(self, name: str) -> int:
        """Return an axis position by name."""
        for index, axis in enumerate(self.axes):
            if axis.name == name:
                return index
        raise KeyError(f'unknown domain axis {name!r}')
