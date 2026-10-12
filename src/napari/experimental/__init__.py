"""Experimental APIs; importing scientific models does not load Layers."""

from importlib import import_module

__all__ = ['layers_linked', 'link_layers', 'unlink_layers']


def __getattr__(name):
    if name in __all__:
        value = getattr(
            import_module('napari.layers.utils._link_layers'), name
        )
        globals()[name] = value
        return value
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__():
    return sorted(__all__)
