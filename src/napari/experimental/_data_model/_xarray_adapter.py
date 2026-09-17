"""Lossless xarray adaptation for the compositional data model."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeAlias

import numpy as np

from napari.experimental._data_model._axis import (
    AxisRole,
    CoordinateAxis,
    CoordinateFrame,
)
from napari.experimental._data_model._data_object import DataObject
from napari.experimental._data_model._domain import StructuredGridDomain
from napari.experimental._data_model._field import Field
from napari.experimental._data_model._graph import FrameRegistry
from napari.experimental._data_model._mapping import CoordinateEmbedding
from napari.experimental._data_model._source import ArraySource
from napari.experimental._data_model._validation import validate_unit

if TYPE_CHECKING:
    import xarray as xr

    from napari.experimental._data_model._axis import AxisDtype

_SLICE_SPACING_RTOL = 1e-6
_SLICE_SPACING_ATOL = 1e-6
_UNIT_VECTOR_ATOL = 1e-6
_BASIS_DETERMINANT_RTOL = 1e-9
_UNSET: Any = object()


@dataclass(frozen=True, slots=True)
class XarrayEmbedding:
    """Describe an affine embedding to construct with an xarray domain.

    ``matrix`` is the spatial block, with target-frame coordinates in rows
    and ``spatial_axis_names`` in columns. The adapter expands it to the full
    domain dimensionality. When ``target_axis_names`` is omitted, the spatial
    domain-axis names are also used for the target frame. ``target_frame``
    retains an identity-stable registry frame when supplied and must match the
    target name and axis metadata.
    """

    target_frame_name: str
    matrix: Any
    offset: Any
    spatial_axis_names: Sequence[str]
    target_axis_units: Sequence[str | None]
    target_axis_names: Sequence[str] | None = None
    target_frame: CoordinateFrame | None = None


EmbeddingInput: TypeAlias = (
    XarrayEmbedding
    | tuple[
        str,
        Any,
        Any,
        Sequence[str],
        Sequence[str | None],
    ]
)


def data_object_from_xarray(
    da: xr.DataArray,
    *,
    name: str | None = None,
    field_unit: str | None = _UNSET,
    missing_value: Any = _UNSET,
    units: Mapping[str, str | None] | None = None,
    axis_roles: Mapping[str, AxisRole] | None = None,
    embedding: EmbeddingInput | None = None,
) -> DataObject:
    """Adapt an xarray DataArray without materializing its field data.

    Dimension coordinates become explicit :class:`CoordinateAxis` values in
    DataArray dimension order. Irregular coordinates are preserved exactly;
    dimensions without coordinates remain index axes. A coordinate's CF-style
    ``units`` attribute takes precedence over the ``units`` mapping. The
    DataArray's own ``units`` and ``_FillValue`` attributes become Field
    metadata unless explicitly overridden. Non-dimension coordinates become
    lazy Fields in ``DataObject.coordinates`` with explicit dimensions, units,
    and missing-value sentinels. They retain their original dtype and order.
    Auxiliary names must be non-empty strings, as required by Field names;
    rename non-string or empty xarray coordinate names before adaptation.
    Auxiliary units that Pint cannot interpret produce a warning and leave the
    Field unit unspecified. Original auxiliary attributes, including those unit
    declarations, remain in ``metadata['xarray:coordinate_attributes']``; no
    unit conversion or interpretation of a time origin is inferred.

    String coordinates are categorical coordinate axes by default. This
    adapter never infers field-component axes: callers may keep an existing
    MAGNITUDE/PHASE dimension as a categorical coordinate axis, or preferably
    pass a complex DataArray and derive displayable magnitude and phase fields
    while retaining the complex field as first-class data.

    Parameters
    ----------
    da : xarray.DataArray
        Labeled array to adapt. Its underlying ``data`` object, not ``values``,
        is wrapped in :class:`ArraySource`.
    name : str or None
        Data-object name. Defaults to the DataArray name, then ``"xarray"``.
    field_unit : str or None, optional
        Field value unit. Defaults to the DataArray ``units`` attribute when
        omitted.
    missing_value : object or None, optional
        Field missing-value sentinel. Defaults to the DataArray ``_FillValue``
        attribute when omitted.
    units : mapping of str to str or None, optional
        Fallback units by dimension name.
    axis_roles : mapping of str to AxisRole, optional
        Overrides for domain-axis roles, most commonly ``AxisRole.LOCAL``.
        Component roles are invalid because component axes belong to Fields.
    embedding : XarrayEmbedding or 5-tuple, optional
        Affine embedding specification. The tuple form is ``(target frame
        name, spatial matrix, offset, spatial axis names, target axis units)``
        and uses the spatial names as target-frame axis names.
    """
    import xarray as xr

    if not isinstance(da, xr.DataArray):
        raise TypeError('da must be an xarray.DataArray')

    unit_map = _validate_axis_mapping(units, da.dims, kind='units')
    role_map = _validate_axis_mapping(axis_roles, da.dims, kind='axis_roles')
    axes = tuple(
        _axis_from_dimension(da, dim, unit_map, role_map) for dim in da.dims
    )
    domain = StructuredGridDomain(axes)

    embeddings: tuple[CoordinateEmbedding, ...] = ()
    if embedding is not None:
        embeddings = (_coordinate_embedding(domain, embedding),)

    field_name = da.name or 'data'
    model_field = Field(
        field_name,
        ArraySource(da.data),
        unit=(da.attrs.get('units') if field_unit is _UNSET else field_unit),
        missing_value=(
            da.attrs.get('_FillValue')
            if missing_value is _UNSET
            else missing_value
        ),
    )
    return DataObject(
        name or da.name or 'xarray',
        domain,
        {field_name: model_field},
        embeddings=embeddings,
        coordinates={
            coordinate_name: Field(
                coordinate_name,
                ArraySource(coordinate.data),
                unit=_auxiliary_unit(
                    coordinate_name, coordinate.attrs.get('units')
                ),
                missing_value=coordinate.attrs.get('_FillValue'),
                dimensions=tuple(coordinate.dims),
            )
            for coordinate_name, coordinate in da.coords.items()
            if coordinate_name not in da.dims
        },
        metadata={
            'xarray:coordinate_attributes': {
                coordinate_name: dict(coordinate.attrs)
                for coordinate_name, coordinate in da.coords.items()
                if coordinate_name not in da.dims
            }
        },
    )


def _auxiliary_unit(name: str, unit: Any) -> str | None:
    try:
        validate_unit(unit, kind=f'coordinate {name!r}')
    except (TypeError, ValueError):
        warnings.warn(
            f'coordinate {name!r} unit {unit!r} is not interpreted; '
            "original attributes are retained in metadata['xarray:coordinate_attributes']",
            UserWarning,
            stacklevel=3,
        )
        return None
    return unit


def embedding_from_reference_frame(
    domain_axis_names: Sequence[str],
    frame: Any,
    *,
    registry: FrameRegistry | None = None,
) -> XarrayEmbedding:
    """Build an xarray embedding specification from a DICOM frame.

    ``frame`` is duck-typed and must expose ``origin``, ``basis_vectors``,
    ``in_plane_spacing``, ``slice_positions``, ``units``, ``convention``, and
    ``frame_of_reference_uid``. This matches pirana's ``ReferenceFrame`` but
    deliberately introduces no pirana dependency.

    The last three ``domain_axis_names`` are the spatial slice, row, and column
    axes, respectively. The geometry contract orders ``basis_vectors`` the
    same way: vector 0 is the slice direction, vector 1 is the row direction,
    and vector 2 is the column direction. ``in_plane_spacing`` is
    correspondingly ``(row, column)``. ``slice_positions`` are distances along
    the slice basis from ``origin``; their first value is folded into the
    returned offset. Pirana's offset-from-origin positions start at zero, while
    producers that supply absolute positions therefore translate the offset.
    Basis-vector components are expressed in the declared convention, so a
    permuted or sign-flipped basis is preserved in the returned matrix.

    Basis vectors must be normalized and linearly independent, but need not be
    orthogonal. Irregular slice positions cannot be represented by the affine
    model and raise :class:`NotImplementedError` rather than being regularized.
    When ``registry`` is provided, identified frames retain an identity-stable
    target frame. Independent adaptations with the same non-``None`` frame UID
    and axes through that registry therefore share one frame instance. A frame
    without a UID never consults the registry and remains distinct per
    adaptation.
    """
    axis_names = _axis_names(domain_axis_names)
    if len(axis_names) < 3:
        raise ValueError('domain must contain at least three axes')
    spatial_axis_names = axis_names[-3:]

    origin = _geometry_array(frame, 'origin', (3,))
    basis_vectors = _geometry_array(frame, 'basis_vectors', (3, 3))
    in_plane_spacing = _geometry_array(frame, 'in_plane_spacing', (2,))
    slice_positions = np.asarray(frame.slice_positions, dtype=float)
    if slice_positions.ndim != 1 or slice_positions.size < 2:
        raise ValueError('slice_positions must contain at least two values')
    if not np.all(np.isfinite(slice_positions)):
        raise ValueError('slice_positions must contain only finite values')

    basis_norms = np.linalg.norm(basis_vectors, axis=1)
    if not np.allclose(
        basis_norms, np.ones(3), rtol=0.0, atol=_UNIT_VECTOR_ATOL
    ):
        raise ValueError('basis_vectors must contain unit vectors')
    determinant_scale = float(np.prod(basis_norms))
    if abs(float(np.linalg.det(basis_vectors))) <= (
        _BASIS_DETERMINANT_RTOL * determinant_scale
    ):
        raise ValueError('basis_vectors must form a nonsingular basis')
    if np.any(in_plane_spacing <= 0.0):
        raise ValueError('in_plane_spacing values must be positive')

    slice_differences = np.diff(slice_positions)
    if not np.allclose(
        slice_differences,
        slice_differences[0],
        rtol=_SLICE_SPACING_RTOL,
        atol=_SLICE_SPACING_ATOL,
    ):
        raise NotImplementedError(
            'irregular slice spacing cannot be represented by an affine '
            'embedding'
        )
    slice_spacing = float(slice_differences[0])
    if np.isclose(slice_spacing, 0.0, rtol=0.0, atol=_SLICE_SPACING_ATOL):
        raise ValueError('slice spacing must be non-zero')

    convention = frame.convention
    if not isinstance(convention, str) or len(convention) != 3:
        raise ValueError('frame convention must be a three-character string')
    target_axis_names = tuple(convention.upper())
    if len(set(target_axis_names)) != 3:
        raise ValueError('frame convention axes must be unique')

    step_sizes = np.array(
        [slice_spacing, in_plane_spacing[0], in_plane_spacing[1]]
    )
    matrix = (basis_vectors * step_sizes[:, np.newaxis]).T
    offset = origin + slice_positions[0] * basis_vectors[0]
    frame_uid = frame.frame_of_reference_uid
    frame_name = (
        convention.upper()
        if frame_uid is None
        else f'{convention.upper()}:{frame_uid}'
    )
    target_axis_units = (frame.units,) * 3
    if registry is not None and not isinstance(registry, FrameRegistry):
        raise TypeError('registry must be a FrameRegistry or None')
    target_frame = (
        None
        if registry is None or frame_uid is None
        else registry.get_or_create(
            frame_name,
            tuple(zip(target_axis_names, target_axis_units, strict=True)),
        )
    )
    return XarrayEmbedding(
        target_frame_name=frame_name,
        matrix=matrix,
        offset=offset,
        spatial_axis_names=spatial_axis_names,
        target_axis_units=target_axis_units,
        target_axis_names=target_axis_names,
        target_frame=target_frame,
    )


def _axis_from_dimension(
    da: xr.DataArray,
    dim: str,
    units: Mapping[str, str | None],
    axis_roles: Mapping[str, AxisRole],
) -> CoordinateAxis:
    role = axis_roles.get(dim, AxisRole.COORDINATE)
    if not isinstance(role, AxisRole):
        raise TypeError('axis_roles values must be AxisRole instances')
    if role is AxisRole.COMPONENT:
        raise ValueError(
            'xarray dimensions are domain axes; component roles are not '
            'supported'
        )

    unit = units.get(dim)
    if dim not in da.coords:
        return CoordinateAxis(
            dim, da.sizes[dim], role=role, unit=unit, dtype_kind='int'
        )

    coordinate = da.coords[dim]
    if coordinate.dims != (dim,):
        raise ValueError(
            f'coordinate {dim!r} must be one-dimensional over its dimension'
        )
    if 'units' in coordinate.attrs:
        unit = coordinate.attrs['units']
    values = np.asarray(coordinate.data)
    dtype_kind = _coordinate_dtype_kind(values.dtype, dim)
    labels = (
        tuple(_categorical_label(value) for value in values)
        if dtype_kind == 'categorical'
        else None
    )
    return CoordinateAxis(
        dim,
        da.sizes[dim],
        role=role,
        unit=unit,
        values=values,
        dtype_kind=dtype_kind,
        categorical_labels=labels,
    )


def _coordinate_dtype_kind(dtype: np.dtype[Any], name: str) -> AxisDtype:
    kinds: dict[str, AxisDtype] = {
        'f': 'float',
        'i': 'int',
        'u': 'int',
        'M': 'datetime',
        'S': 'categorical',
        'U': 'categorical',
    }
    try:
        return kinds[dtype.kind]
    except KeyError as exc:
        raise TypeError(
            f'coordinate {name!r} has unsupported dtype {dtype}'
        ) from exc


def _categorical_label(value: Any) -> str:
    if isinstance(value, bytes | np.bytes_):
        return bytes(value).decode()
    return str(value)


def _validate_axis_mapping(
    value: Mapping[str, Any] | None,
    dimensions: Sequence[str],
    *,
    kind: str,
) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f'{kind} must be a mapping')
    unknown = tuple(name for name in value if name not in dimensions)
    if unknown:
        raise ValueError(
            f'{kind} contains unknown dimensions: ' + ', '.join(unknown)
        )
    return value


def _coordinate_embedding(
    domain: StructuredGridDomain, embedding: EmbeddingInput
) -> CoordinateEmbedding:
    if isinstance(embedding, tuple):
        if len(embedding) != 5:
            raise ValueError('embedding tuple must contain five items')
        embedding = XarrayEmbedding(*embedding)
    if not isinstance(embedding, XarrayEmbedding):
        raise TypeError('embedding must be an XarrayEmbedding or 5-tuple')

    spatial_axis_names = _axis_names(embedding.spatial_axis_names)
    if len(spatial_axis_names) != len(set(spatial_axis_names)):
        raise ValueError('embedding spatial axis names must be unique')
    try:
        spatial_axis_indices = tuple(
            domain.axis_index(name) for name in spatial_axis_names
        )
    except KeyError as exc:
        raise ValueError(exc.args[0]) from exc

    spatial_matrix = np.asarray(embedding.matrix, dtype=float)
    if spatial_matrix.ndim != 2 or spatial_matrix.shape[1] != len(
        spatial_axis_names
    ):
        raise ValueError(
            'embedding matrix columns must match the spatial axis names'
        )
    target_axis_units = tuple(embedding.target_axis_units)
    target_axis_names = (
        spatial_axis_names
        if embedding.target_axis_names is None
        else _axis_names(embedding.target_axis_names)
    )
    target_ndim = spatial_matrix.shape[0]
    if len(target_axis_names) != target_ndim:
        raise ValueError(
            'embedding target axis names must match the matrix rows'
        )
    if len(target_axis_units) != target_ndim:
        raise ValueError(
            'embedding target axis units must match the matrix rows'
        )

    matrix = np.zeros((target_ndim, len(domain.axes)), dtype=float)
    matrix[:, spatial_axis_indices] = spatial_matrix
    target_frame_axes = tuple(
        zip(target_axis_names, target_axis_units, strict=True)
    )
    if embedding.target_frame is None:
        target_frame = CoordinateFrame(
            embedding.target_frame_name, target_frame_axes
        )
    else:
        target_frame = embedding.target_frame
        if not isinstance(target_frame, CoordinateFrame):
            raise TypeError('embedding target_frame must be a CoordinateFrame')
        if target_frame.name != embedding.target_frame_name:
            raise ValueError(
                'embedding target_frame name must match target_frame_name'
            )
        actual_axes = tuple(
            (axis.name, axis.unit) for axis in target_frame.axes
        )
        if actual_axes != target_frame_axes:
            raise ValueError(
                'embedding target_frame axes must match target axis metadata'
            )
    return CoordinateEmbedding(domain, target_frame, matrix, embedding.offset)


def _axis_names(names: Sequence[str]) -> tuple[str, ...]:
    if isinstance(names, str) or not isinstance(names, Sequence):
        raise TypeError('axis names must be a sequence of strings')
    result = tuple(names)
    if not all(isinstance(name, str) for name in result):
        raise TypeError('axis names must be a sequence of strings')
    return result


def _geometry_array(
    frame: Any, attribute: str, shape: tuple[int, ...]
) -> np.ndarray:
    values = np.asarray(getattr(frame, attribute), dtype=float)
    if values.shape != shape:
        raise ValueError(f'frame {attribute} must have shape {shape}')
    if not np.all(np.isfinite(values)):
        raise ValueError(f'frame {attribute} must contain only finite values')
    return values
