"""Measure zero-alpha triangle filtering in the Shapes VisPy adapter.

This is an investigation probe, not a benchmark suite. It monkeypatches
``MeshVisual.set_data`` so baseline and candidate rounds can share the same
viewer and layer. The layer uses ``translucent_no_depth`` because omitting
zero-alpha fragments is not pixel-equivalent in every napari blending mode.
"""

import argparse
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import vispy
from qtpy import API_NAME
from qtpy.QtCore import qVersion
from qtpy.QtWidgets import QApplication
from vispy.gloo import gl
from vispy.visuals import MeshVisual

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
from napari.settings import get_settings  # noqa: E402
from napari.utils.triangulation_backend import (  # noqa: E402
    TriangulationBackend,
    set_backend,
)


def make_rectangles(count: int, layout: str) -> np.ndarray:
    indices = np.arange(count)
    z = indices % 2
    planar = indices // 2
    if layout == 'overlap':
        offset = (planar % 16) / 16
        return np.stack(
            (
                np.column_stack((z, offset, offset)),
                np.column_stack((z, offset, 256 + offset)),
                np.column_stack((z, 256 + offset, 256 + offset)),
                np.column_stack((z, 256 + offset, offset)),
            ),
            axis=1,
        )

    width = int(np.ceil(np.sqrt((count + 1) // 2)))
    y = (planar // width) * 3
    x = (planar % width) * 3
    return np.stack(
        (
            np.column_stack((z, y, x)),
            np.column_stack((z, y, x + 2)),
            np.column_stack((z, y + 2, x + 2)),
            np.column_stack((z, y + 2, x)),
        ),
        axis=1,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--mode',
        choices=('baseline', 'triangles', 'compact', 'paired'),
        required=True,
    )
    parser.add_argument(
        '--style', choices=('fill', 'outline', 'both'), required=True
    )
    parser.add_argument('--shapes', type=int, default=8192)
    parser.add_argument(
        '--layout', choices=('grid', 'overlap'), default='grid'
    )
    parser.add_argument(
        '--paired-candidate',
        choices=('triangles', 'compact'),
        default='compact',
    )
    parser.add_argument('--repeats', type=int, default=12)
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


def _image_difference(first: np.ndarray, second: np.ndarray) -> dict[str, int]:
    difference = np.abs(first.astype(np.int16) - second.astype(np.int16))
    return {
        'max_channel_difference': int(np.max(difference)),
        'different_channel_values': int(np.count_nonzero(difference)),
    }


def _timing_summary(samples: list[float]) -> dict[str, object]:
    return {
        'median_ms': float(np.median(samples)),
        'p25_ms': float(np.percentile(samples, 25)),
        'p75_ms': float(np.percentile(samples, 75)),
        'p90_ms': float(np.percentile(samples, 90)),
        'samples_ms': samples,
    }


def _percent_summary(samples: list[float]) -> dict[str, object]:
    return {
        'median_percent': float(np.median(samples)),
        'p25_percent': float(np.percentile(samples, 25)),
        'p75_percent': float(np.percentile(samples, 75)),
        'samples_percent': samples,
    }


def _gl_string(parameter: str) -> str:
    value = gl.glGetParameter(parameter)
    return value.decode() if isinstance(value, bytes) else str(value)


def main() -> None:
    args = parse_args()
    settings = get_settings()
    if settings.experimental.async_:
        raise RuntimeError(
            'This probe requires synchronous slicing. Use a scratch '
            'NAPARI_CONFIG with experimental.async_ disabled.'
        )
    set_backend(TriangulationBackend.fastest_available)

    active_mode = 'baseline' if args.mode == 'paired' else args.mode
    original_set_data = MeshVisual.set_data
    target_visual = None
    upload: defaultdict[str, int] = defaultdict(int)

    def measured_set_data(
        self,
        vertices=None,
        faces=None,
        vertex_colors=None,
        face_colors=None,
        color=None,
        vertex_values=None,
        meshdata=None,
    ):
        if (
            self is target_visual
            and faces is not None
            and face_colors is not None
            and len(faces) == len(face_colors)
        ):
            input_triangles = len(faces)
            input_transparent = int(np.count_nonzero(face_colors[:, 3] == 0))
            visible = face_colors[:, 3] != 0
            if active_mode != 'baseline' and not np.all(visible):
                faces = faces[visible]
                face_colors = face_colors[visible]
                if active_mode == 'compact' and len(faces):
                    used_vertices = np.zeros(len(vertices), dtype=bool)
                    used_vertices[faces] = True
                    remap = np.cumsum(used_vertices, dtype=faces.dtype) - 1
                    vertices = vertices[used_vertices]
                    faces = remap[faces]

            upload['set_data_calls'] += 1
            upload['input_triangles'] += input_triangles
            upload['input_transparent_triangles'] += input_transparent
            upload['vertices'] += len(vertices)
            upload['triangles'] += len(faces)
            upload['dropped_triangles'] += input_triangles - len(faces)
            upload['uploaded_transparent_triangles'] += int(
                np.count_nonzero(face_colors[:, 3] == 0)
            )

        return original_set_data(
            self,
            vertices=vertices,
            faces=faces,
            vertex_colors=vertex_colors,
            face_colors=face_colors,
            color=color,
            vertex_values=vertex_values,
            meshdata=meshdata,
        )

    MeshVisual.set_data = measured_set_data
    app = QApplication.instance() or QApplication([])
    viewer = None
    try:
        viewer = napari.Viewer(show=False)
        transparent = np.array([0.2, 0.4, 0.8, 0.0])
        opaque_face = np.array([0.2, 0.4, 0.8, 1.0])
        opaque_edge = np.array([0.9, 0.4, 0.1, 1.0])
        face_color = transparent if args.style == 'outline' else opaque_face
        edge_color = transparent if args.style == 'fill' else opaque_edge
        layer = viewer.add_shapes(
            make_rectangles(args.shapes, args.layout),
            shape_type='rectangle',
            face_color=face_color,
            edge_color=edge_color,
            edge_width=1,
            blending='translucent_no_depth',
        )
        canvas = viewer.window._qt_viewer.canvas
        scene_canvas = canvas._scene_canvas
        target_visual = canvas.layer_to_visual[layer].node.shape_faces
        viewer.reset_view()
        viewer.dims.set_current_step(0, 1)
        app.processEvents()

        def draw_and_finish():
            scene_canvas.set_current()
            canvas.on_draw(None)
            scene_canvas._draw_scene()
            gl.glFinish()

        def slice_round_trip():
            viewer.dims.set_current_step(0, 0)
            app.processEvents()
            draw_and_finish()
            viewer.dims.set_current_step(0, 1)
            app.processEvents()
            draw_and_finish()

        warmup_modes = (
            ('baseline', args.paired_candidate)
            if args.mode == 'paired'
            else (args.mode,)
        )
        screenshots = {}
        for mode in warmup_modes:
            active_mode = mode
            slice_round_trip()
            canvas.on_draw(None)
            screenshots[mode] = scene_canvas.render()
        for repeat in range(2):
            modes = warmup_modes if repeat % 2 == 0 else warmup_modes[::-1]
            for mode in modes:
                active_mode = mode
                slice_round_trip()
        upload.clear()

        mesh = layer._data_view._mesh
        displayed_transparent_triangles = int(
            np.count_nonzero(mesh.displayed_triangles_colors[:, 3] == 0)
        )
        common_result = {
            'mode': args.mode,
            'style': args.style,
            'shapes': args.shapes,
            'layout': args.layout,
            'blending': layer.blending,
            'repeats': args.repeats,
            'model_vertices': len(mesh.vertices),
            'displayed_triangles': len(mesh.displayed_triangles),
            'displayed_transparent_triangles': (
                displayed_transparent_triangles
            ),
            'environment': {
                'platform': platform.platform(),
                'python': sys.version.split()[0],
                'napari': napari.__version__,
                'numpy': np.__version__,
                'vispy': vispy.__version__,
                'bermuda': _package_version('bermuda'),
                'qt_api': API_NAME,
                'qt': qVersion(),
                'gl_renderer': _gl_string(gl.GL_RENDERER),
                'gl_version': _gl_string(gl.GL_VERSION),
                'resolved_triangulation_backend': _resolved_shape_backend(
                    layer
                ),
                'async_slicing': settings.experimental.async_,
                'settings_source': _settings_source,
            },
        }

        if args.mode == 'paired':
            paired_samples: defaultdict[str, list[float]] = defaultdict(list)
            paired_upload: dict[str, defaultdict[str, int]] = {
                mode: defaultdict(int) for mode in warmup_modes
            }
            for repeat in range(args.repeats):
                modes = warmup_modes
                if repeat % 2:
                    modes = modes[::-1]
                for mode in modes:
                    active_mode = mode
                    upload.clear()
                    start = time.perf_counter_ns()
                    slice_round_trip()
                    paired_samples[mode].append(
                        (time.perf_counter_ns() - start) / 1e6
                    )
                    for key, value in upload.items():
                        paired_upload[mode][key] += value

            baseline_triangles = paired_upload['baseline']['triangles']
            candidate_triangles = paired_upload[args.paired_candidate][
                'triangles'
            ]
            filter_expected = displayed_transparent_triangles > 0
            filter_applied = candidate_triangles < baseline_triangles
            baseline_samples = paired_samples['baseline']
            candidate_samples = paired_samples[args.paired_candidate]
            paired_differences = [
                baseline - candidate
                for baseline, candidate in zip(
                    baseline_samples, candidate_samples, strict=True
                )
            ]
            paired_percentages = [
                (baseline - candidate) / baseline * 100
                for baseline, candidate in zip(
                    baseline_samples, candidate_samples, strict=True
                )
            ]
            result = {
                **common_result,
                'timings': {
                    mode: _timing_summary(samples)
                    for mode, samples in paired_samples.items()
                },
                'paired_baseline_minus_candidate': {
                    'milliseconds': _timing_summary(paired_differences),
                    'relative': _percent_summary(paired_percentages),
                },
                'upload_by_mode': {
                    mode: dict(counts)
                    for mode, counts in paired_upload.items()
                },
                'filter_validation': {
                    'filter_expected': filter_expected,
                    'filter_applied': filter_applied,
                    'valid': not filter_expected or filter_applied,
                },
                'pixel_difference': _image_difference(
                    screenshots['baseline'],
                    screenshots[args.paired_candidate],
                ),
            }
        else:
            samples = []
            for _ in range(args.repeats):
                upload.clear()
                start = time.perf_counter_ns()
                slice_round_trip()
                samples.append((time.perf_counter_ns() - start) / 1e6)
            result = {
                **common_result,
                'median_ms': float(np.median(samples)),
                'p90_ms': float(np.percentile(samples, 90)),
                'samples_ms': samples,
                'upload_last_round': dict(upload),
            }
        print(json.dumps(result))
    finally:
        if viewer is not None:
            viewer.close()
        MeshVisual.set_data = original_set_data


if __name__ == '__main__':
    main()
