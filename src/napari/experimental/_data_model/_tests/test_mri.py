import copy
import pickle

import numpy as np
import pytest

from napari.experimental._data_model import (
    DataObject,
    DerivedSource,
    synthetic_mri,
)


def test_synthetic_mri_composes_signal_and_derived_fields() -> None:
    data_object = synthetic_mri()

    assert data_object.domain.shape == (4, 5, 3, 16, 24, 24)
    assert tuple(axis.name for axis in data_object.domain.axes) == (
        'time',
        'echo_time',
        'flip_angle',
        'z',
        'y',
        'x',
    )
    np.testing.assert_array_equal(
        data_object.domain.axis('echo_time').values,
        [2.0, 4.5, 9.0, 15.0, 22.5],
    )
    np.testing.assert_array_equal(
        data_object.domain.axis('flip_angle').values, [5.0, 15.0, 30.0]
    )
    assert all(
        data_object.domain.axis(name).values is None
        for name in ('z', 'y', 'x')
    )

    signal = data_object.fields['signal']
    magnitude = data_object.fields['magnitude']
    phase = data_object.fields['phase']
    assert signal.dtype == np.dtype(np.complex64)
    assert isinstance(magnitude.source, DerivedSource)
    assert isinstance(phase.source, DerivedSource)
    assert magnitude.source.source is signal.source
    assert phase.source.source is signal.source
    assert signal.unit is None
    assert magnitude.unit is None
    assert phase.unit == 'rad'

    point = (
        slice(1, 2),
        slice(2, 3),
        slice(1, 2),
        slice(4, 5),
        slice(6, 7),
        slice(8, 9),
    )
    signal_value = signal.read(point)
    np.testing.assert_allclose(magnitude.read(point), np.abs(signal_value))
    np.testing.assert_allclose(phase.read(point), np.angle(signal_value))


def _assert_mri_field_structure_matches(
    copied: DataObject, original: DataObject
) -> None:
    assert type(copied) is type(original)
    assert tuple(copied.fields) == tuple(original.fields)
    assert {
        name: model_field.shape for name, model_field in copied.fields.items()
    } == {
        name: model_field.shape
        for name, model_field in original.fields.items()
    }


def test_synthetic_mri_supports_deepcopy_with_immutable_public_mappings() -> (
    None
):
    original = synthetic_mri()

    copied = copy.deepcopy(original)

    _assert_mri_field_structure_matches(copied, original)
    with pytest.raises(TypeError):
        copied.fields['other'] = copied.fields['signal']  # type: ignore[index]
    with pytest.raises(TypeError):
        copied.domain.axes[0].aux_values['other'] = np.ones(4)  # type: ignore[index]


def test_synthetic_mri_supports_pickle_round_trip() -> None:
    original = synthetic_mri()

    restored = pickle.loads(pickle.dumps(original))

    _assert_mri_field_structure_matches(restored, original)


def test_synthetic_mri_embedding_places_only_spatial_axes() -> None:
    data_object = synthetic_mri()

    embedding = data_object.embeddings[0]
    assert embedding.target_frame.name == 'scanner'
    assert tuple(axis.unit for axis in embedding.target_frame.axes) == (
        'mm',
        'mm',
        'mm',
    )
    expected_matrix = np.zeros((3, 6))
    expected_matrix[:, -3:] = np.diag([2.0, 0.9, 0.9])
    np.testing.assert_allclose(embedding.matrix, expected_matrix)
    np.testing.assert_allclose(embedding.offset, [-12.0, -10.0, 5.0])


def test_replace_preserves_fields_and_aux_values() -> None:
    obj = synthetic_mri()
    renamed = copy.replace(obj, name='renamed')
    assert renamed.name == 'renamed'
    assert sorted(renamed.fields) == sorted(obj.fields)
    assert renamed.metadata == obj.metadata

    also_renamed = obj.replace(name='also renamed')
    assert also_renamed.name == 'also renamed'
    assert sorted(also_renamed.fields) == sorted(obj.fields)

    echo_axis = obj.domain.axis('echo_time')
    replaced_axis = copy.replace(echo_axis, name='te')
    assert replaced_axis.name == 'te'
    assert list(replaced_axis.aux_values) == list(echo_axis.aux_values)
    np.testing.assert_array_equal(replaced_axis.values, echo_axis.values)


def test_dataclasses_replace_rejects_data_objects() -> None:
    import dataclasses

    obj = synthetic_mri()
    with pytest.raises(TypeError):
        dataclasses.replace(obj, name='x')


def test_default_metadata_is_not_shared_between_instances() -> None:
    first = synthetic_mri()
    second = synthetic_mri()
    first.metadata['probe'] = 1
    assert 'probe' not in second.metadata


def test_repr_names_fields_and_aux_values() -> None:
    obj = synthetic_mri()
    assert 'magnitude' in repr(obj)
    assert 'echo_time' in repr(obj.domain.axis('echo_time'))
