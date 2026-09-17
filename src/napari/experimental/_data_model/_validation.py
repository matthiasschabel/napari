from __future__ import annotations


def validate_name(name: str, *, kind: str) -> None:
    """Validate a public model name."""
    if not isinstance(name, str):
        raise TypeError(f'{kind} name must be a string')
    if not name.strip():
        raise ValueError(f'{kind} name must not be empty')


def validate_unit(unit: str | None, *, kind: str) -> None:
    """Validate a unit spelling accepted by napari's Pint registry.

    Model objects retain their input strings, while napari layers may normalize
    those spellings when constructing Pint units. Pint accepts some surprising
    spellings: for example, ``"a.u."`` parses as atomic mass unit multiplied
    by year. Use ``None`` for unitless data instead of informal labels.
    """
    if unit is None:
        return
    if not isinstance(unit, str):
        raise TypeError(f'{kind} unit must be a string or None')
    from pint import get_application_registry
    from pint.errors import PintError

    try:
        # napari.utils._units.get_unit_registry uses this application registry.
        get_application_registry().parse_units(unit)
    except (PintError, TypeError) as exc:
        raise ValueError(
            f'{kind} unit {unit!r} is not pint-compatible'
        ) from exc
