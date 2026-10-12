from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import FrozenInstanceError, dataclass
from numbers import Integral
from types import MappingProxyType
from typing import Any

from napari.experimental._data_model._domain import (
    Domain,
    validate_domain,
)
from napari.experimental._data_model._field import Field
from napari.experimental._data_model._mapping import CoordinateEmbedding
from napari.experimental._data_model._validation import validate_name

_EMPTY_FIELDS: Mapping[str, Field] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class _ScientificState:
    name: str
    domain: Domain
    fields: dict[str, Field]
    embeddings: tuple[CoordinateEmbedding, ...]
    metadata: dict[str, Any]
    coordinates: dict[str, Field]


class DataObject:
    """Identity-bearing scientific state, independent of its consumers.

    ``update`` validates a complete replacement state before publishing it and
    notifying subscribers. ``replace`` (also ``copy.replace``) creates a new
    identity sharing storage. Direct attribute assignment is prohibited.
    ``dataclasses.replace`` is unsupported and raises TypeError.

    The field mapping and embedding sequence are read-only. Construction and
    ``replace`` copy top-level metadata; nested metadata, embeddings and source
    buffers retain their ownership.
    Updates and notifications are synchronous and must be serialized by callers.
    This contract does not make concurrent external buffer writes atomic.

    ``coordinates`` contains Fields with explicit named dimension associations.
    They describe acquisition variables without broadcasting over every field
    dimension. Coordinate storage remains lazy; coordinate selection indexes and
    coupled coordinate/value snapshots are not provided by this aggregate.

    Parameters
    ----------
    name : str
        Object name.
    domain : Domain
        Intrinsic structured or geometric domain.
    fields : mapping of str to Field
        Ordered fields keyed by their own names.
    embeddings : sequence of CoordinateEmbedding
        Mappings from this domain's intrinsic frame.
    metadata : dict or None
        Uninterpreted scientific metadata. The top-level dictionary is copied.
    coordinates : mapping of str to Field
        Coordinate variables with explicit dimensions, keyed by their own names.
        This namespace is separate from fields; a name may appear in both.
    """

    __slots__ = ('_notifying', '_revision', '_state', '_subscribers')

    def __init__(
        self,
        name: str,
        domain: Domain,
        fields: Mapping[str, Field] = _EMPTY_FIELDS,
        embeddings: Sequence[CoordinateEmbedding] = (),
        metadata: dict[str, Any] | None = None,
        *,
        coordinates: Mapping[str, Field] = _EMPTY_FIELDS,
    ) -> None:
        state = self._validate_state(
            name,
            domain,
            fields,
            embeddings,
            {} if metadata is None else metadata,
            coordinates,
        )
        object.__setattr__(self, '_state', state)
        object.__setattr__(self, '_revision', 0)
        object.__setattr__(self, '_subscribers', {})
        object.__setattr__(self, '_notifying', False)

    def __setattr__(self, name: str, value: Any) -> None:
        raise FrozenInstanceError(
            'use DataObject.update to change scientific state'
        )

    @property
    def name(self) -> str:
        return self._state.name

    @property
    def domain(self) -> Domain:
        return self._state.domain

    @property
    def embeddings(self) -> tuple[CoordinateEmbedding, ...]:
        return self._state.embeddings

    @property
    def metadata(self) -> dict[str, Any]:
        return self._state.metadata

    @property
    def coordinates(self) -> Mapping[str, Field]:
        """Lazy coordinate variables and their explicit dimension associations."""
        return MappingProxyType(self._state.coordinates)

    @property
    def revision(self) -> int:
        """Number of successfully published descriptor updates.

        In-place metadata or external source-buffer edits do not increment this
        counter. It is not a storage revision or a consistent-read token.
        """
        return self._revision

    def isel(self, selections: Mapping[str, Any]) -> DataObject:
        """Return a lazy positional selection, retaining selected dimensions.

        Scalars, slices and integer vectors follow NumPy indexing. Coordinate
        variables are selected with their associated fields, preserving order.
        Non-affine spatial gathers and multiscale sources are unsupported.
        """
        from napari.experimental._data_model._index_selection import isel

        return isel(self, selections)

    def sel(
        self, selections: Mapping[str, Any], *, duplicates: str = 'error'
    ) -> DataObject:
        """Return an exact coordinate selection; integers are coordinate values.

        Scalar one-dimensional coordinates are supported. Choose ``'all'`` to
        retain repeated matches; otherwise duplicates require disambiguation.
        Rebuild the selection after editing its lookup coordinate sources.
        """
        from napari.experimental._data_model._index_selection import sel

        return sel(self, selections, duplicates=duplicates)

    def update(self, **changes: Any) -> None:
        """Publish validated descriptors while retaining this object's identity.

        Invalid changes leave state and revision untouched. Each successful call
        with changes increments the revision once. All subscribers are notified,
        even if one raises; notification errors are then raised as ExceptionGroup
        with the update already committed. Reentrant updates are rejected.
        """
        if self._notifying:
            raise RuntimeError('cannot update during DataObject notification')
        if not changes:
            return
        candidate = self.replace(**changes)
        object.__setattr__(self, '_state', candidate._state)
        object.__setattr__(self, '_revision', self.revision + 1)
        errors = []
        object.__setattr__(self, '_notifying', True)
        try:
            for token, callback in tuple(self._subscribers.items()):
                if token not in self._subscribers:
                    continue
                try:
                    callback(self)
                except Exception as exc:  # noqa: BLE001  # Deliver to all subscribers before raising.
                    errors.append(exc)
        finally:
            object.__setattr__(self, '_notifying', False)
        if errors:
            raise ExceptionGroup(
                'DataObject update committed; subscriber failed', errors
            )

    def subscribe(
        self, callback: Callable[[DataObject], None]
    ) -> Callable[[], None]:
        """Subscribe to committed updates and return an idempotent disconnect.

        Callbacks are held strongly until disconnected. Subscriptions belong to
        consumers and are excluded from copies and serialization. Disconnecting
        during notification prevents any remaining call to that subscription;
        subscriptions added during notification start on the next update.
        Repeated subscriptions to the same callback have independent lifetimes.
        """
        if not callable(callback):
            raise TypeError('subscriber must be callable')

        token = object()
        self._subscribers[token] = callback

        def unsubscribe() -> None:
            self._subscribers.pop(token, None)

        return unsubscribe

    def __getstate__(self) -> tuple[_ScientificState, int]:
        return self._state, self.revision

    def __setstate__(self, state: tuple[_ScientificState, int]) -> None:
        object.__setattr__(self, '_state', state[0])
        object.__setattr__(self, '_revision', state[1])
        object.__setattr__(self, '_subscribers', {})
        object.__setattr__(self, '_notifying', False)

    def __replace__(self, **changes: Any) -> DataObject:
        """Copy one captured descriptor state, then apply replacements.

        Serves ``copy.replace`` (3.13+) and the ``replace`` method.
        Use the returned object for consistent multi-property descriptor reads.
        Source buffers, embeddings and nested metadata remain shared; this is
        not a snapshot of their mutable contents.
        """
        current = self._state
        state: dict[str, Any] = {
            'name': current.name,
            'domain': current.domain,
            'fields': dict(current.fields),
            'embeddings': current.embeddings,
            'metadata': dict(current.metadata),
            'coordinates': dict(current.coordinates),
        }
        state.update(changes)
        return DataObject(**state)

    replace = __replace__

    def __repr__(self) -> str:
        return (
            f'DataObject(name={self.name!r}, domain={self.domain!r}, '
            f'fields={list(self.fields)!r}, '
            f'embeddings={len(self.embeddings)}, metadata={self.metadata!r})'
        )

    @staticmethod
    def _validate_state(
        name: str,
        domain: Domain,
        fields: Mapping[str, Field],
        embeddings: Sequence[CoordinateEmbedding],
        metadata: dict[str, Any],
        coordinates: Mapping[str, Field],
    ) -> _ScientificState:
        validate_name(name, kind='data object')
        validate_domain(domain)
        if not isinstance(fields, Mapping):
            raise TypeError('fields must be an ordered mapping by name')
        fields = dict(fields)
        for field_name, model_field in fields.items():
            validate_name(field_name, kind='field key')
            if not isinstance(model_field, Field):
                raise TypeError('field values must be Field instances')
            if field_name != model_field.name:
                raise ValueError('field mapping key must match the field name')
            DataObject._validate_field(domain, model_field)

        if not isinstance(coordinates, Mapping):
            raise TypeError('coordinates must be a mapping by name')
        coordinates = dict(coordinates)
        for coordinate_name, coordinate in coordinates.items():
            validate_name(coordinate_name, kind='coordinate key')
            if not isinstance(coordinate, Field):
                raise TypeError('coordinate values must be Field instances')
            if coordinate_name != coordinate.name:
                raise ValueError(
                    'coordinate mapping key must match the field name'
                )
            if coordinate.dimensions is None:
                raise ValueError(
                    'coordinate fields require explicit dimensions'
                )
            DataObject._validate_field(domain, coordinate)

        if not isinstance(embeddings, Sequence):
            raise TypeError('embeddings must be a sequence')
        embeddings = tuple(embeddings)
        for embedding in embeddings:
            if not isinstance(embedding, CoordinateEmbedding):
                raise TypeError(
                    'embeddings must be CoordinateEmbedding instances'
                )
            if (
                embedding.domain is not domain
                or embedding.source_frame is not domain.intrinsic_frame
            ):
                raise ValueError(
                    'embedding source must be this domain intrinsic frame'
                )

        if not isinstance(metadata, dict):
            raise TypeError('metadata must be a dictionary')
        return _ScientificState(
            name, domain, fields, embeddings, dict(metadata), coordinates
        )

    @property
    def fields(self) -> Mapping[str, Field]:
        """Ordered fields as a read-only mapping."""
        return MappingProxyType(self._state.fields)

    @staticmethod
    def _validate_field(domain: Domain, model_field: Field) -> None:
        domain_axes = model_field.domain_storage_axes
        if model_field.dimensions is None:
            expected_shape = domain.shape
        else:
            resolve = getattr(domain, 'association_shape', None)
            if not callable(resolve):
                raise TypeError(
                    'named field dimensions require a domain association_shape method'
                )
            expected_shape = resolve(model_field.dimensions)
            if (
                not isinstance(expected_shape, tuple)
                or len(expected_shape) != len(model_field.dimensions)
                or any(
                    isinstance(size, bool)
                    or not isinstance(size, Integral)
                    or size < 0
                    for size in expected_shape
                )
            ):
                raise TypeError(
                    'domain association_shape must return one nonnegative integer per dimension'
                )
        if len(domain_axes) != len(expected_shape):
            raise ValueError(
                f'field {model_field.name!r} has {len(domain_axes)} '
                f'non-component axes but the domain has '
                f'{len(expected_shape)} axes'
            )
        field_domain_shape = tuple(
            model_field.shape[axis] for axis in domain_axes
        )
        if field_domain_shape != expected_shape:
            raise ValueError(
                f'field {model_field.name!r} non-component shape '
                f'{field_domain_shape} does not match domain shape '
                f'{expected_shape}'
            )
