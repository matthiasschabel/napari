"""Fixtures shared by the data-model test modules."""

from __future__ import annotations

import pytest


@pytest.fixture
def headless_vispy(monkeypatch):
    from vispy import config

    import napari._vispy.canvas as canvas_module
    import napari._vispy.layers.base as base_module
    import napari._vispy.layers.image as image_module

    # The offscreen QPA provides no GL context or usable macOS display DPI.
    # Pin DPI separately; the monkeypatches affect GL probe functions only.
    max_texture_sizes = (16384, 2048)
    original_dpi = config['dpi']
    config['dpi'] = 96
    monkeypatch.setattr(
        canvas_module, 'get_max_texture_sizes', lambda: max_texture_sizes
    )
    monkeypatch.setattr(
        base_module, 'get_max_texture_sizes', lambda: max_texture_sizes
    )
    monkeypatch.setattr(
        image_module, 'get_max_texture_sizes', lambda: max_texture_sizes
    )
    monkeypatch.setattr(image_module, 'get_gl_extensions', lambda: '')
    try:
        yield
    finally:
        config['dpi'] = original_dpi
