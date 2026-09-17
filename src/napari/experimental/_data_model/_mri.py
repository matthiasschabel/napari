from __future__ import annotations

import numpy as np

from napari.experimental._data_model._axis import (
    CoordinateAxis,
    CoordinateFrame,
)
from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._derived import DerivedSource
from napari.experimental._data_model._domain import StructuredGridDomain
from napari.experimental._data_model._field import Field
from napari.experimental._data_model._mapping import CoordinateEmbedding
from napari.experimental._data_model._source import ArraySource

_MRI_SHAPE = (4, 5, 3, 16, 24, 24)
_ECHO_TIMES_MS = np.array([2.0, 4.5, 9.0, 15.0, 22.5])
_FLIP_ANGLES_DEG = np.array([5.0, 15.0, 30.0])
_SPATIAL_SPACING_MM = (2.0, 0.9, 0.9)
_SPATIAL_OFFSET_MM = (-12.0, -10.0, 5.0)


def synthetic_mri() -> DataObject:
    """Return a small six-dimensional complex MRI acquisition.

    Magnitude and phase are lazy pointwise views of the complex signal. The
    three spatial domain axes remain in index space and are placed in a scanner
    frame by one anisotropic affine embedding.
    """
    nt, ne, nf, nz, ny, nx = _MRI_SHAPE
    time_values = np.arange(nt, dtype=float) * 2.0
    axes = (
        CoordinateAxis('time', nt, unit='s', values=time_values),
        CoordinateAxis('echo_time', ne, unit='ms', values=_ECHO_TIMES_MS),
        CoordinateAxis('flip_angle', nf, unit='deg', values=_FLIP_ANGLES_DEG),
        CoordinateAxis('z', nz, unit='mm'),
        CoordinateAxis('y', ny, unit='mm'),
        CoordinateAxis('x', nx, unit='mm'),
    )
    domain = StructuredGridDomain(axes)

    z = np.linspace(-1.0, 1.0, nz)[:, None, None]
    y = np.linspace(-1.0, 1.0, ny)[None, :, None]
    x = np.linspace(-1.0, 1.0, nx)[None, None, :]
    t2_star = 18.0 + 12.0 * (1.0 - (z**2 + y**2 + x**2) / 3.0)
    spatial_magnitude = 1.0 + 0.15 * x + 0.08 * y
    magnitude = (
        (1.0 + 0.025 * time_values[:, None, None, None, None, None])
        * np.exp(
            -_ECHO_TIMES_MS[None, :, None, None, None, None]
            / t2_star[None, None, None, :, :, :]
        )
        * np.sin(np.deg2rad(_FLIP_ANGLES_DEG[None, None, :, None, None, None]))
        * spatial_magnitude[None, None, None, :, :, :]
    )
    phase = 0.45 * z + 0.2 * y - 0.15 * x
    signal_values = (
        magnitude * np.exp(1j * phase[None, None, None, :, :, :])
    ).astype(np.complex64)

    signal_source = ArraySource(signal_values)
    fields = {
        'signal': Field('signal', signal_source, unit=None),
        'magnitude': Field(
            'magnitude', DerivedSource(signal_source, np.abs), unit=None
        ),
        'phase': Field(
            'phase', DerivedSource(signal_source, np.angle), unit='rad'
        ),
    }

    scanner = CoordinateFrame(
        'scanner', (('z', 'mm'), ('y', 'mm'), ('x', 'mm'))
    )
    matrix = np.zeros((3, len(domain.axes)), dtype=float)
    matrix[:, -3:] = np.diag(_SPATIAL_SPACING_MM)
    embedding = CoordinateEmbedding(
        domain, scanner, matrix, _SPATIAL_OFFSET_MM
    )
    return DataObject(
        'synthetic MRI',
        domain,
        fields,
        embeddings=(embedding,),
        metadata={'modality': 'MRI'},
    )
