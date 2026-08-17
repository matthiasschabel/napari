"""Measure total-layer work during Shapes creation and movement.

The "overlay" cases keep the large layer immutable and update a one-shape
layer. They are an implementation-independent ceiling for a private active-edit
overlay, not a production workaround.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np

_settings_dir = tempfile.TemporaryDirectory(
    prefix='napari-shapes-active-edit-'
)
os.environ.setdefault(
    'NAPARI_CONFIG', str(Path(_settings_dir.name) / 'settings.yaml')
)

import napari  # noqa: E402
from napari.layers import Shapes  # noqa: E402


def create_paths(n_shapes: int, n_rays: int, seed: int = 0) -> np.ndarray:
    """Create deterministic radial paths matching issues #8399 and #8650."""
    rng = np.random.default_rng(seed)
    radius = 500 / np.sqrt(n_shapes)
    centers = rng.uniform(0, 1000, (n_shapes, 2))
    phi = np.linspace(0, 2 * np.pi, n_rays, endpoint=False)
    rays = np.stack([np.sin(phi), np.cos(phi)], axis=1)[None, ...]
    rays = rays * rng.uniform(0.9, 1.1, (n_shapes, n_rays, 2))
    return centers[:, None, :] + radius * rays


def _median_ms(samples: list[float]) -> float:
    return 1000 * statistics.median(samples)


def _draw_and_finish(canvas) -> None:
    """Draw immediately and wait for GPU completion."""
    scene_canvas = canvas._scene_canvas
    scene_canvas.set_current()
    canvas.on_draw(None)
    scene_canvas._draw_scene()
    scene_canvas.context.finish()


def _refresh(layer: Shapes) -> None:
    layer.refresh(thumbnail=False, highlight=False, extent=False)


def _measure_shift(layer: Shapes, canvas, repeats: int) -> dict[str, float]:
    index = len(layer.data) - 1
    delta = np.array([0.125, -0.25])
    mutate_samples = []
    refresh_samples = []
    dirty_draw_samples = []
    total_samples = []
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for phase in (1, -1) * ((repeats + 1) // 2):
            if len(total_samples) == repeats:
                break
            started = time.perf_counter()
            layer._data_view.shift(index, phase * delta)
            mutated = time.perf_counter()
            _refresh(layer)
            refreshed = time.perf_counter()
            _draw_and_finish(canvas)
            finished = time.perf_counter()
            mutate_samples.append(mutated - started)
            refresh_samples.append(refreshed - mutated)
            dirty_draw_samples.append(finished - refreshed)
            total_samples.append(finished - started)
    finally:
        if gc_was_enabled:
            gc.enable()

    frame_s = statistics.median(total_samples)
    return {
        'mutate_ms': _median_ms(mutate_samples),
        'refresh_set_data_ms': _median_ms(refresh_samples),
        'dirty_draw_finish_ms': _median_ms(dirty_draw_samples),
        'full_frame_ms': 1000 * frame_s,
        'fps': 1 / frame_s,
    }


def _summarize(samples: list[float]) -> dict[str, float]:
    return {
        'median_ms': _median_ms(samples),
        'max_ms': 1000 * max(samples),
        'first_ms': 1000 * samples[0],
        'last_ms': 1000 * samples[-1],
    }


def _measure_growth(
    layer: Shapes, canvas, path
) -> dict[str, dict[str, float]]:
    index = len(layer.data) - 1
    mutate_samples = []
    refresh_samples = []
    dirty_draw_samples = []
    total_samples = []
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        for stop in range(3, len(path) + 1):
            started = time.perf_counter()
            layer._data_view.edit(index, path[:stop])
            mutated = time.perf_counter()
            _refresh(layer)
            refreshed = time.perf_counter()
            _draw_and_finish(canvas)
            finished = time.perf_counter()
            mutate_samples.append(mutated - started)
            refresh_samples.append(refreshed - mutated)
            dirty_draw_samples.append(finished - refreshed)
            total_samples.append(finished - started)
    finally:
        if gc_was_enabled:
            gc.enable()

    return {
        'mutate': _summarize(mutate_samples),
        'refresh_set_data': _summarize(refresh_samples),
        'dirty_draw_finish': _summarize(dirty_draw_samples),
        'full_frame': _summarize(total_samples),
    }


def run_case(n_shapes: int, n_rays: int, repeats: int) -> dict:
    paths = create_paths(n_shapes, n_rays)
    viewer = napari.Viewer(show=False)
    try:
        canvas = viewer.window._qt_viewer.canvas
        started = time.perf_counter()
        main = viewer.add_shapes(paths, shape_type='path', edge_color='coral')
        add_layer_s = time.perf_counter() - started
        _draw_and_finish(canvas)

        initial_mesh = main._data_view._mesh
        mesh_vertices = len(initial_mesh.vertices)
        mesh_triangles = len(initial_mesh.displayed_triangles)
        payload_bytes = sum(
            array.nbytes
            for array in (
                initial_mesh.vertices,
                initial_mesh.displayed_triangles,
                initial_mesh.displayed_triangles_colors,
            )
        )

        normal_shift = _measure_shift(main, canvas, repeats)
        overlay = viewer.add_shapes(
            [np.array(main.data[-1], copy=True)],
            shape_type='path',
            edge_color='coral',
        )
        _draw_and_finish(canvas)
        overlay_shift = _measure_shift(overlay, canvas, repeats)

        new_path = create_paths(1, n_rays, seed=1)[0]
        main.add(new_path[:2], shape_type='path', edge_color='coral', gui=True)
        _draw_and_finish(canvas)
        direct_growth = _measure_growth(main, canvas, new_path)

        growth_overlay = viewer.add_shapes(
            [new_path[:2]], shape_type='path', edge_color='coral'
        )
        _draw_and_finish(canvas)
        overlay_growth = _measure_growth(growth_overlay, canvas, new_path)

        started = time.perf_counter()
        main.add(new_path, shape_type='path', edge_color='coral')
        added = time.perf_counter()
        _draw_and_finish(canvas)
        finished = time.perf_counter()

        return {
            'n_shapes': n_shapes,
            'n_rays': n_rays,
            'add_layer_s': add_layer_s,
            'mesh_vertices': mesh_vertices,
            'mesh_triangles': mesh_triangles,
            'mesh_payload_bytes': int(payload_bytes),
            'vispy_indexed_vertex_components': int(
                mesh_triangles * 3 * main._slice_input.ndisplay
            ),
            'normal_shift': normal_shift,
            'overlay_shift': overlay_shift,
            'direct_main_growth': direct_growth,
            'overlay_growth': overlay_growth,
            'overlay_commit': {
                'model_refresh_set_data_ms': 1000 * (added - started),
                'dirty_draw_finish_ms': 1000 * (finished - added),
                'total_ms': 1000 * (finished - started),
            },
        }
    finally:
        viewer.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--shapes', type=int, nargs='+', required=True)
    parser.add_argument('--rays', type=int, default=32)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    results = [
        run_case(n_shapes, args.rays, args.repeats) for n_shapes in args.shapes
    ]
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
