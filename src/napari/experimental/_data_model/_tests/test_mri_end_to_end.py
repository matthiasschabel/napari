from __future__ import annotations

import numpy as np

from napari.components import ViewerModel
from napari.experimental._data_model import (
    CoordinateSelection,
    DataObject,
    DerivedSource,
    Field,
    add_to_viewer,
    synthetic_mri,
)

_TRANSFORM_ATOL = 1e-12


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


def _with_counting_signal(
    data_object: DataObject,
) -> tuple[DataObject, CountingSource]:
    signal_values = data_object.fields['signal'].source.array
    source = CountingSource(signal_values)
    counted = DataObject(
        data_object.name,
        data_object.domain,
        {
            'signal': Field('signal', source, unit=None),
            'magnitude': Field(
                'magnitude', DerivedSource(source, np.abs), unit=None
            ),
            'phase': Field(
                'phase', DerivedSource(source, np.angle), unit='rad'
            ),
        },
        embeddings=data_object.embeddings,
        metadata=data_object.metadata,
    )
    return counted, source


def _is_strict_subregion(
    region: tuple[slice, ...], shape: tuple[int, ...]
) -> bool:
    return any(
        len(range(*item.indices(size))) < size
        for item, size in zip(region, shape, strict=True)
    )


def test_synthetic_mri_viewer_functional_slice() -> None:
    data_object, counting_source = _with_counting_signal(synthetic_mri())
    viewer = ViewerModel()

    magnitude = add_to_viewer(viewer, data_object, 'magnitude')
    phase = add_to_viewer(viewer, data_object, 'phase')

    assert [layer.name for layer in viewer.layers] == [
        'synthetic MRI:magnitude',
        'synthetic MRI:phase',
    ]
    assert viewer.dims.ndim == 6
    assert viewer.dims.ndisplay == 2
    assert viewer.dims.axis_labels == (
        'time',
        'echo_time',
        'flip_angle',
        'z',
        'y',
        'x',
    )

    viewer.dims.set_current_step(1, 0)
    first_echo = magnitude._data_view.copy()
    viewer.dims.set_current_step(1, 1)
    second_echo = magnitude._data_view.copy()
    assert not np.allclose(first_echo, second_echo)

    embedding = data_object.embeddings[0]
    spatial_shape = np.array(data_object.domain.shape[-3:])
    expected_spatial_extent = np.stack(
        (
            embedding.offset,
            embedding.offset
            + np.diag(embedding.matrix[:, -3:]) * (spatial_shape - 1),
        )
    )
    np.testing.assert_allclose(
        magnitude.extent.world[:, -3:],
        expected_spatial_extent,
        rtol=0.0,
        atol=_TRANSFORM_ATOL,
    )
    assert tuple(str(unit) for unit in magnitude.units) == (
        'second',
        'millisecond',
        'degree',
        'millimeter',
        'millimeter',
        'millimeter',
    )
    irregular_axes = magnitude.metadata['napari:irregular_axes']
    np.testing.assert_array_equal(
        irregular_axes['echo_time'],
        data_object.domain.axis('echo_time').values,
    )
    np.testing.assert_array_equal(
        irregular_axes['flip_angle'],
        data_object.domain.axis('flip_angle').values,
    )
    assert magnitude.metadata['napari:data_object'] is data_object
    assert phase.metadata['napari:data_object'] is data_object

    viewer.dims.set_current_step((0, 1, 2, 3), (1, 2, 1, 4))
    model_slice = CoordinateSelection(
        {
            'time': 1,
            'echo_time': 8.8,
            'flip_angle': 1,
            'z': 4,
        },
        method='nearest',
    ).read(data_object, 'signal')
    np.testing.assert_allclose(
        magnitude._data_view,
        np.abs(model_slice),
        rtol=1e-6,
        atol=1e-7,
    )

    assert counting_source.regions
    assert all(
        _is_strict_subregion(region, counting_source.shape)
        for region in counting_source.regions
    )
