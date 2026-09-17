from __future__ import annotations

from typing import Any

import numpy as np

from napari.experimental._data_model._axis import CoordinateFrame
from napari.experimental._data_model._domain import Domain, validate_domain

_MAX_INVERSE_CONDITION = 1e12


class AffineMapping:
    """Ordered affine mapping between two coordinate frames.

    Parameters
    ----------
    source_frame : CoordinateFrame
        Frame in which input points are expressed.
    target_frame : CoordinateFrame
        Frame in which output points are expressed.
    matrix : array-like
        Matrix with shape ``(target_frame.ndim, source_frame.ndim)``.
    offset : array-like or None
        Target-frame offset. The default is zero.
    """

    __slots__ = ('_matrix', '_offset', 'source_frame', 'target_frame')

    def __init__(
        self,
        source_frame: CoordinateFrame,
        target_frame: CoordinateFrame,
        matrix: Any,
        offset: Any | None = None,
    ) -> None:
        if not isinstance(source_frame, CoordinateFrame) or not isinstance(
            target_frame, CoordinateFrame
        ):
            raise TypeError(
                'source and target must be CoordinateFrame objects'
            )
        affine_matrix = np.asarray(matrix, dtype=float)
        expected_shape = (target_frame.ndim, source_frame.ndim)
        if affine_matrix.shape != expected_shape:
            raise ValueError(
                f'affine matrix shape must be {expected_shape}, got '
                f'{affine_matrix.shape}'
            )
        affine_offset = (
            np.zeros(target_frame.ndim, dtype=float)
            if offset is None
            else np.asarray(offset, dtype=float)
        )
        if affine_offset.shape != (target_frame.ndim,):
            raise ValueError(
                'affine offset shape must match the target frame dimension'
            )

        self.source_frame = source_frame
        self.target_frame = target_frame
        self._matrix = affine_matrix.copy()
        self._offset = affine_offset.copy()
        self._matrix.flags.writeable = False
        self._offset.flags.writeable = False

    @property
    def matrix(self) -> np.ndarray:
        """Read-only affine matrix."""
        return self._matrix

    @property
    def offset(self) -> np.ndarray:
        """Read-only affine offset."""
        return self._offset

    def map_points(self, points: Any, *, frame: CoordinateFrame) -> np.ndarray:
        """Map points from the declared source frame.

        Parameters
        ----------
        points : array-like
            Points whose final dimension is ``source_frame.ndim``.
        frame : CoordinateFrame
            Frame identity attached to ``points``.

        Returns
        -------
        numpy.ndarray
            Points in ``target_frame``.
        """
        if frame is not self.source_frame:
            raise ValueError('points are not expressed in the source frame')
        point_array = np.asarray(points, dtype=float)
        if (
            point_array.ndim == 0
            or point_array.shape[-1] != self.source_frame.ndim
        ):
            raise ValueError(
                'point coordinates must match the source frame dimension'
            )
        return point_array @ self.matrix.T + self.offset

    def inverse(self) -> AffineMapping:
        """Return the inverse mapping.

        Raises
        ------
        NotImplementedError
            If the matrix is non-square, singular, or too ill-conditioned for
            a reliable inverse.
        """
        if self.matrix.shape[0] != self.matrix.shape[1]:
            raise NotImplementedError(
                'only square affine mappings can be inverted'
            )
        try:
            inverse_matrix = np.linalg.inv(self.matrix)
        except np.linalg.LinAlgError as exc:
            raise NotImplementedError('affine mapping is singular') from exc
        condition = float(np.linalg.cond(self.matrix))
        if not np.isfinite(condition) or condition > _MAX_INVERSE_CONDITION:
            raise NotImplementedError(
                'affine mapping is near-singular: condition number '
                f'{condition:.3g} exceeds {_MAX_INVERSE_CONDITION:.0e}'
            )
        inverse_offset = -(inverse_matrix @ self.offset)
        return AffineMapping(
            self.target_frame,
            self.source_frame,
            inverse_matrix,
            inverse_offset,
        )


class CoordinateEmbedding(AffineMapping):
    """Affine mapping from a domain's intrinsic frame to another frame."""

    __slots__ = ('domain',)

    def __init__(
        self,
        domain: Domain,
        target_frame: CoordinateFrame,
        matrix: Any,
        offset: Any | None = None,
    ) -> None:
        validate_domain(domain, kind='embedding domain')
        self.domain = domain
        super().__init__(
            domain.intrinsic_frame,
            target_frame,
            matrix,
            offset,
        )


_EMBEDDING_ROW_RTOL = 1e-9


def _embedding_coordinates(
    embedding: CoordinateEmbedding,
) -> tuple[
    tuple[int, ...],
    np.ndarray,
    np.ndarray,
    tuple[str | None, ...],
]:
    """Analyze a diagonal embedding in target-axis order.

    Returns the domain axis, scale, translation, and unit corresponding to
    every target-frame axis. Coupled target rows and repeated domain axes are
    rejected here so all model-level view composition uses the same policy.
    """
    domain_axes: list[int] = []
    for row in embedding.matrix:
        row_norm = np.linalg.norm(row)
        nonzero = np.flatnonzero(np.abs(row) > _EMBEDDING_ROW_RTOL * row_norm)
        if len(nonzero) != 1:
            raise NotImplementedError(
                'coordinate embedding target rows must touch exactly one '
                'domain axis; off-diagonal coupling is not supported'
            )
        domain_axis = int(nonzero[0])
        domain_axes.append(domain_axis)
    if len(domain_axes) != len(set(domain_axes)):
        raise NotImplementedError(
            'coordinate embedding target rows must map to distinct domain '
            'axes; off-diagonal coupling is not supported'
        )
    spatial_scale = np.array(
        [
            embedding.matrix[target_axis, domain_axis]
            for target_axis, domain_axis in enumerate(domain_axes)
        ]
    )
    spatial_units = tuple(axis.unit for axis in embedding.target_frame.axes)
    return (
        tuple(domain_axes),
        spatial_scale,
        embedding.offset.copy(),
        spatial_units,
    )
