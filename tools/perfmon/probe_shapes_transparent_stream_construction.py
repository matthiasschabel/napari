"""Measure transparent-edge construction costs for Shapes.

Plain wall-clock timings provide the reported denominator. ``--profile`` runs
one additional construction under cProfile for hotspot discovery; its elapsed
time is deliberately not used in the reported ratios.
"""

import argparse
import cProfile
import gc
import importlib.metadata
import json
import os
import platform
import pstats
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import vispy

# Importing napari resolves the settings path, so isolate it first unless the
# caller explicitly supplied a scratch configuration.
_settings_temp_dir = None
if 'NAPARI_CONFIG' not in os.environ:
    _settings_temp_dir = tempfile.TemporaryDirectory(
        prefix='napari-transparent-stream-probe-'
    )
    os.environ['NAPARI_CONFIG'] = str(
        Path(_settings_temp_dir.name) / 'settings.yaml'
    )
    _settings_source = 'temporary'
else:
    _settings_source = 'environment'

import napari  # noqa: E402
from napari.layers import Shapes  # noqa: E402
from napari.settings import get_settings  # noqa: E402
from napari.utils.triangulation_backend import (  # noqa: E402
    TriangulationBackend,
    get_backend,
    set_backend,
)


def make_data(count: int, shape: str) -> np.ndarray:
    indices = np.arange(count)
    width = int(np.ceil(np.sqrt(count)))
    y = (indices // width) * 6
    x = (indices % width) * 6
    if shape == 'rectangle':
        return np.stack(
            (
                np.column_stack((y, x)),
                np.column_stack((y, x + 2)),
                np.column_stack((y + 2, x + 2)),
                np.column_stack((y + 2, x)),
            ),
            axis=1,
        )

    angles = np.linspace(0, 2 * np.pi, 20, endpoint=False)
    radii = np.tile((2.5, 1.25), 10)
    offsets = np.column_stack((radii * np.sin(angles), radii * np.cos(angles)))
    return (np.stack((y, x), axis=1)[:, None, :] + offsets[None, :, :]).astype(
        np.float32
    )


def summarize(samples: list[float]) -> dict[str, Any]:
    return {
        'median_s': float(np.median(samples)),
        'min_s': float(np.min(samples)),
        'p25_s': float(np.percentile(samples, 25)),
        'p75_s': float(np.percentile(samples, 75)),
        'samples_s': samples,
    }


def time_call(callable_: Callable[[], Any], repeats: int) -> dict[str, Any]:
    samples = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        result = callable_()
        samples.append(time.perf_counter() - start)
        del result
    return summarize(samples)


def time_pair(
    first: Callable[[], Any],
    second: Callable[[], Any],
    repeats: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    samples = {'first': [], 'second': []}
    for repeat in range(repeats):
        order = (('first', first), ('second', second))
        if repeat % 2:
            order = order[::-1]
        for name, callable_ in order:
            gc.collect()
            start = time.perf_counter()
            result = callable_()
            samples[name].append(time.perf_counter() - start)
            del result

    paired_differences = [
        first_sample - second_sample
        for first_sample, second_sample in zip(
            samples['first'], samples['second'], strict=True
        )
    ]
    return (
        summarize(samples['first']),
        summarize(samples['second']),
        summarize(paired_differences),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=4096)
    parser.add_argument(
        '--shape', choices=('rectangle', 'polygon'), default='polygon'
    )
    parser.add_argument(
        '--backend', choices=('fastest', 'pure'), default='fastest'
    )
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--profile', action='store_true')
    return parser.parse_args()


def _package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _resolved_shape_backend(layer) -> str:
    method_name = layer._data_view.shapes[0]._set_meshes.__name__
    return {
        '_set_meshes_compiled_bermuda': 'bermuda',
        '_set_meshes_compiled_partseg': 'partsegcore',
        '_set_meshes_triangle': 'triangle',
        '_set_meshes_py': 'python_or_numba',
    }.get(method_name, method_name)


def main() -> None:
    args = parse_args()
    settings = get_settings()
    requested_backend = (
        TriangulationBackend.fastest_available
        if args.backend == 'fastest'
        else TriangulationBackend.pure_python
    )
    set_backend(requested_backend)
    data = make_data(args.count, args.shape)

    def construct_layer(layer_data=data):
        return Shapes(
            layer_data,
            shape_type=args.shape,
            face_color='blue',
            edge_color='transparent',
        )

    warmup_layer = construct_layer(data[:1])
    resolved_backend = _resolved_shape_backend(warmup_layer)
    layer_timings = time_call(construct_layer, args.repeats)
    layer_median = layer_timings['median_s']
    result: dict[str, Any] = {
        'requested_backend': str(get_backend()),
        'resolved_backend': resolved_backend,
        'shape': args.shape,
        'count': args.count,
        'repeats': args.repeats,
        'layer': layer_timings,
        'environment': {
            'platform': platform.platform(),
            'python': sys.version.split()[0],
            'napari': napari.__version__,
            'numpy': np.__version__,
            'vispy': vispy.__version__,
            'bermuda': _package_version('bermuda'),
            'async_slicing': settings.experimental.async_,
            'settings_source': _settings_source,
        },
    }

    if args.shape == 'polygon' and resolved_backend == 'bermuda':
        import bermuda

        def triangulate_combined():
            return [
                bermuda.triangulate_polygons_with_edge([polygon])
                for polygon in data
            ]

        def triangulate_face():
            return [
                bermuda.triangulate_polygons_face([polygon])
                for polygon in data
            ]

        def triangulate_edge():
            return [
                bermuda.triangulate_path_edge(polygon, closed=True)
                for polygon in data
            ]

        triangulate_combined()
        triangulate_face()
        triangulate_edge()
        combined, face, combined_minus_face = time_pair(
            triangulate_combined, triangulate_face, args.repeats
        )
        edge = time_call(triangulate_edge, args.repeats)
        result['bermuda'] = {
            'combined': combined,
            'face': face,
            'edge': edge,
            'paired_combined_minus_face': combined_minus_face,
            'edge_fraction_of_layer': edge['median_s'] / layer_median,
            'paired_combined_minus_face_fraction_of_layer': (
                combined_minus_face['median_s'] / layer_median
            ),
        }
    elif args.shape == 'polygon':
        result['bermuda'] = {
            'skipped': f'resolved backend is {resolved_backend}'
        }

    print(json.dumps(result))

    if args.profile:
        profile = cProfile.Profile()
        profile.enable()
        construct_layer()
        profile.disable()
        pstats.Stats(profile, stream=sys.stderr).strip_dirs().sort_stats(
            'cumtime'
        ).print_stats(35)


if __name__ == '__main__':
    main()
