from __future__ import annotations

from itertools import product
from numbers import Real
from typing import Any

import numpy as np

from napari.experimental._data_model._annotation import ROI
from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._domain import StructuredGridDomain
from napari.experimental._data_model._geometry import contains_xy
from napari.experimental._data_model._mapping import _embedding_coordinates
from napari.experimental._data_model._selection import CoordinateSelection
from napari.experimental._data_model._source import (
    SourceChangedError,
    source_revision,
)


def mean_over_roi(
    obj: DataObject,
    roi: ROI,
    field: str,
    sample_axes: tuple[str, ...] = (),
    *,
    tolerance: float | None = None,
) -> dict[Any, float]:
    """Return masked means from one anchored plane without xarray.

    Polygon containment is evaluated at pixel-center world coordinates from
    the embedding whose target frame is the anchor frame. Containment uses
    strict-interior semantics: pixel centers exactly on exterior or hole
    boundaries are excluded. The anchor context must pin every non-spatial
    axis that is not named in ``sample_axes``. One sample axis produces scalar
    coordinate keys; multiple axes produce tuple keys in the requested order.
    With no sample axes, the single key is ``()``.

    Parameters
    ----------
    obj : DataObject
        Structured-grid object annotated by ``roi``.
    roi : ROI
        Multipolygon annotation attached to ``obj`` by identity.
    field : str
        Scalar field to average.
    sample_axes : tuple of str
        Non-spatial axes over which to report separate means.
    tolerance : float or None
        Maximum world-coordinate distance from the nearest target slice.
        Defaults to half the spacing along the anchor's plane axis.
    """
    if not isinstance(obj, DataObject):
        raise TypeError('obj must be a DataObject')
    if not isinstance(obj.domain, StructuredGridDomain):
        raise TypeError('mean_over_roi requires a StructuredGridDomain')
    if not isinstance(roi, ROI):
        raise TypeError('roi must be an ROI')
    if not roi.is_valid:
        raise ValueError(
            'ROI target domain changed; recreate or revalidate the anchor'
        )
    if roi.target is not obj:
        raise ValueError('roi target must be obj by identity')
    if isinstance(sample_axes, (str, bytes)) or not isinstance(
        sample_axes, tuple
    ):
        raise TypeError('sample_axes must be a tuple of axis names')
    if not all(isinstance(name, str) and name.strip() for name in sample_axes):
        raise ValueError('sample_axes must contain non-empty axis names')
    if len(sample_axes) != len(set(sample_axes)):
        raise ValueError('sample_axes must be unique')
    if tolerance is not None:
        if isinstance(tolerance, (bool, np.bool_)) or not isinstance(
            tolerance, Real
        ):
            raise TypeError('tolerance must be a real number or None')
        tolerance = float(tolerance)
        if not np.isfinite(tolerance):
            raise ValueError('tolerance must be finite')
        if tolerance < 0:
            raise ValueError('tolerance must be non-negative')

    try:
        model_field = obj.fields[field]
    except KeyError as exc:
        raise KeyError(f'unknown field {field!r}') from exc
    revision = (
        obj.revision,
        roi.data.revision,
        source_revision(model_field.source),
    )
    model_field.require_full_grid(tuple(axis.name for axis in obj.domain.axes))
    if model_field.component_axes:
        raise NotImplementedError('mean_over_roi requires a scalar field')

    embeddings = tuple(
        embedding
        for embedding in obj.embeddings
        if embedding.target_frame is roi.anchor.frame
    )
    if len(embeddings) != 1:
        raise ValueError(
            'mean_over_roi requires exactly one embedding into the anchor frame'
        )
    embedding = embeddings[0]
    if embedding.target_frame.ndim != 3:
        raise ValueError('mean_over_roi requires a three-axis anchor frame')
    spatial_axes, spatial_scale, spatial_offset, _ = _embedding_coordinates(
        embedding
    )

    domain_axes = obj.domain.axes
    domain_axis_by_name = {
        axis.name: index for index, axis in enumerate(domain_axes)
    }
    target_axis_by_name = {
        axis.name: index
        for index, axis in enumerate(embedding.target_frame.axes)
    }
    spatial_domain_axis_by_name = {
        name: spatial_axes[target_axis]
        for name, target_axis in target_axis_by_name.items()
    }
    non_spatial_axes = tuple(
        index for index in range(len(domain_axes)) if index not in spatial_axes
    )
    non_spatial_names = {domain_axes[index].name for index in non_spatial_axes}

    unknown_sample_axes = set(sample_axes) - non_spatial_names
    if unknown_sample_axes:
        raise ValueError('sample_axes must name non-spatial domain axes')
    unknown_context_axes = set(roi.anchor.context) - non_spatial_names
    if unknown_context_axes:
        raise ValueError('ROI context must name non-spatial domain axes')
    overlap = set(sample_axes).intersection(roi.anchor.context)
    if overlap:
        raise ValueError('sample_axes must not be pinned by the ROI context')
    unpinned = non_spatial_names - set(sample_axes) - set(roi.anchor.context)
    if unpinned:
        raise ValueError(
            'ROI context must pin every non-sampled non-spatial axis'
        )

    plane_target_axis = target_axis_by_name[roi.anchor.plane_axis]
    plane_domain_axis = spatial_domain_axis_by_name[roi.anchor.plane_axis]
    continuous_plane_index = (
        roi.anchor.plane_value - spatial_offset[plane_target_axis]
    ) / spatial_scale[plane_target_axis]
    plane_index = int(np.rint(continuous_plane_index))
    if plane_index < 0 or plane_index >= domain_axes[plane_domain_axis].size:
        raise ValueError('ROI plane lies outside the target domain')
    resolved_plane_value = (
        spatial_offset[plane_target_axis]
        + spatial_scale[plane_target_axis] * plane_index
    )
    plane_tolerance = (
        abs(spatial_scale[plane_target_axis]) / 2
        if tolerance is None
        else tolerance
    )
    if abs(roi.anchor.plane_value - resolved_plane_value) > plane_tolerance:
        raise ValueError(
            'ROI plane does not match a target slice within tolerance'
        )

    in_plane_domain_axes = tuple(
        spatial_domain_axis_by_name[name] for name in roi.anchor.in_plane_axes
    )
    world_coordinates = []
    for name, domain_axis in zip(
        roi.anchor.in_plane_axes, in_plane_domain_axes, strict=True
    ):
        target_axis = target_axis_by_name[name]
        world_coordinates.append(
            spatial_offset[target_axis]
            + spatial_scale[target_axis]
            * np.arange(domain_axes[domain_axis].size)
        )
    coordinate_grids = np.meshgrid(*world_coordinates, indexing='ij')
    points = np.column_stack([grid.ravel() for grid in coordinate_grids])
    geometry = roi.data.domain.as_multipolygon()
    mask = np.asarray(
        contains_xy(geometry, points[:, 0], points[:, 1]), dtype=bool
    )
    mask = mask.reshape(tuple(len(values) for values in world_coordinates))
    if not np.any(mask):
        raise ValueError('ROI contains no in-plane pixel centers')

    base_selection = {
        name: _context_selection_value(
            domain_axes[domain_axis_by_name[name]], value
        )
        for name, value in roi.anchor.context.items()
    }
    base_selection[domain_axes[plane_domain_axis].name] = plane_index
    sample_domain_axes = tuple(
        domain_axis_by_name[name] for name in sample_axes
    )
    sample_ranges = tuple(
        range(domain_axes[index].size) for index in sample_domain_axes
    )
    combinations = product(*sample_ranges) if sample_ranges else ((),)

    means: dict[Any, float] = {}
    for sample_indices in combinations:
        selection = dict(base_selection)
        key_values = []
        for name, domain_axis, sample_index in zip(
            sample_axes,
            sample_domain_axes,
            sample_indices,
            strict=True,
        ):
            selection[name] = sample_index
            key_values.append(
                _axis_value(domain_axes[domain_axis], sample_index)
            )

        values = CoordinateSelection(selection, method='exact').read(
            obj, field
        )
        remaining_domain_axes = tuple(
            index
            for index in range(len(domain_axes))
            if domain_axes[index].name not in selection
        )
        if set(remaining_domain_axes) != set(in_plane_domain_axes):
            raise ValueError(
                'selection must leave exactly the ROI in-plane axes'
            )
        transpose_order = tuple(
            remaining_domain_axes.index(axis) for axis in in_plane_domain_axes
        )
        in_plane_values = np.transpose(values, transpose_order)
        if np.iscomplexobj(in_plane_values):
            raise TypeError('mean_over_roi requires a real-valued field')
        key: Any = key_values[0] if len(key_values) == 1 else tuple(key_values)
        selected_values = in_plane_values[mask]
        if model_field.missing_value is not None:
            missing = model_field.missing_value
            if (
                np.asarray(missing).ndim == 0
                and np.issubdtype(np.asarray(missing).dtype, np.inexact)
                and np.isnan(missing)
            ):
                selected_values = selected_values[~np.isnan(selected_values)]
            else:
                selected_values = selected_values[selected_values != missing]
        if not selected_values.size:
            raise ValueError('ROI contains no valid samples')
        means[key] = float(np.mean(selected_values))
    if revision != (
        obj.revision,
        roi.data.revision,
        source_revision(model_field.source),
    ):
        raise SourceChangedError(
            'scientific data changed during ROI measurement'
        )
    return means


def _axis_value(axis: Any, index: int) -> Any:
    if axis.categorical_labels is not None:
        return axis.categorical_labels[index]
    if axis.values is None:
        return index
    value = axis.values[index]
    return value.item() if isinstance(value, np.generic) else value


def _context_selection_value(axis: Any, value: Any) -> Any:
    # CoordinateSelection reserves integers for positional indexing, whereas
    # PlaneAnchor context always stores physical coordinate values.
    if (
        axis.values is not None
        and isinstance(value, (int, np.integer))
        and not isinstance(value, (bool, np.bool_))
    ):
        return float(value)
    return value
