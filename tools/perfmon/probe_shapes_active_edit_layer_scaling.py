"""Measure active-overlay scaling at a fixed total Shapes primitive count."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

_settings_dir = tempfile.TemporaryDirectory(
    prefix='napari-shapes-layer-scaling-'
)
os.environ.setdefault(
    'NAPARI_CONFIG', str(Path(_settings_dir.name) / 'settings.yaml')
)

from probe_shapes_active_edit_overlay import (  # noqa: E402
    _draw_and_finish,
    _measure_shift,
    create_paths,
)

import napari  # noqa: E402


def run_case(total_shapes: int, layer_count: int, repeats: int) -> dict:
    if total_shapes % layer_count:
        raise ValueError('total shapes must be divisible by layer count')

    paths = create_paths(total_shapes, 32)
    per_layer = total_shapes // layer_count
    viewer = napari.Viewer(show=False)
    try:
        layers = []
        for index in range(layer_count):
            start = index * per_layer
            stop = start + per_layer
            layers.append(
                viewer.add_shapes(
                    paths[start:stop],
                    shape_type='path',
                    edge_color='coral',
                )
            )

        canvas = viewer.window._qt_viewer.canvas
        _draw_and_finish(canvas)
        normal = _measure_shift(layers[-1], canvas, repeats)

        overlay = viewer.add_shapes(
            [layers[-1].data[-1]],
            shape_type='path',
            edge_color='coral',
        )
        _draw_and_finish(canvas)
        overlay_result = _measure_shift(overlay, canvas, repeats)
        return {
            'total_shapes': total_shapes,
            'layer_count': layer_count,
            'shapes_per_layer': per_layer,
            'normal_shift': normal,
            'overlay_shift': overlay_result,
        }
    finally:
        viewer.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--total-shapes', type=int, required=True)
    parser.add_argument('--layers', type=int, nargs='+', required=True)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    results = [
        run_case(args.total_shapes, count, args.repeats)
        for count in args.layers
    ]
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
