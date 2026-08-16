# See "Writing benchmarks" in the asv docs for more information.
# https://asv.readthedocs.io/en/latest/writing_benchmarks.html
# or the napari documentation on benchmarking
# https://github.com/napari/napari/blob/main/docs/BENCHMARKS.md

import numpy as np
from qtpy.QtWidgets import QApplication

import napari

from .utils import Skip

LAYER_COUNTS = [1, 16]
GEOMETRIC_LAYER_TYPES = ['points', 'shapes', 'vectors']
RASTER_LAYER_TYPES = ['image', 'binary_labels', 'labels']

GEOMETRIC_TOTALS = {
    'points': 32_768,
    'shapes': 2_048,
    'vectors': 16_384,
}
RASTER_EXTENT = 1024
TOTAL_RASTER_PIXELS = RASTER_EXTENT**2
GEOMETRIC_EXTENT = 256


def _unsupported_geometric_partition(layer_type, n_layers):
    return GEOMETRIC_TOTALS[layer_type] % (2 * n_layers) != 0


def _unsupported_raster_partition(_layer_type, n_layers):
    grid_width = int(np.sqrt(n_layers))
    side = int(np.sqrt(TOTAL_RASTER_PIXELS // n_layers))
    return (
        grid_width**2 != n_layers or side**2 * n_layers != TOTAL_RASTER_PIXELS
    )


def _points_data(n_points):
    rng = np.random.default_rng(0)
    z = np.arange(n_points) % 2
    displayed = rng.uniform(0, GEOMETRIC_EXTENT, size=(n_points, 2))
    return np.column_stack((z, displayed))


def _shapes_data(n_shapes):
    indices = np.arange(n_shapes)
    z = indices % 2
    planar_indices = indices // 2
    grid_width = int(np.ceil(np.sqrt(np.ceil(n_shapes / 2))))
    spacing = GEOMETRIC_EXTENT / grid_width
    y = (planar_indices // grid_width) * spacing
    x = (planar_indices % grid_width) * spacing
    inset = spacing / 5

    return np.stack(
        (
            np.column_stack((z, y + inset, x + inset)),
            np.column_stack((z, y + inset, x + spacing - inset)),
            np.column_stack((z, y + spacing - inset, x + spacing - inset)),
            np.column_stack((z, y + spacing - inset, x + inset)),
        ),
        axis=1,
    )


def _vectors_data(n_vectors):
    starts = _points_data(n_vectors)
    projections = np.zeros_like(starts)
    projections[:, 1:] = 2
    return np.stack((starts, projections), axis=1)


def _geometric_data(layer_type, n_primitives):
    if layer_type == 'points':
        return _points_data(n_primitives)
    if layer_type == 'shapes':
        return _shapes_data(n_primitives)
    if layer_type == 'vectors':
        return _vectors_data(n_primitives)
    raise ValueError(f'Unknown geometric layer type: {layer_type}')


def _split_geometric_data(data, n_layers):
    assert len(data) % (2 * n_layers) == 0
    paired_slices = data.reshape((-1, 2, *data.shape[1:]))
    return [
        paired_slices[index::n_layers].reshape((-1, *data.shape[1:]))
        for index in range(n_layers)
    ]


def _add_geometric_layer(viewer, layer_type, data=None):
    if layer_type == 'points':
        viewer.add_points(data if data is not None else np.empty((0, 3)))
    elif layer_type == 'shapes':
        if data is None:
            viewer.add_shapes(ndim=3)
        else:
            viewer.add_shapes(data, shape_type='rectangle')
    elif layer_type == 'vectors':
        viewer.add_vectors(data if data is not None else np.empty((0, 2, 3)))
    else:
        raise ValueError(f'Unknown geometric layer type: {layer_type}')


def _raster_data(layer_type, side):
    row, column = np.indices((side, side))
    if layer_type == 'image':
        plane = ((row + column) % 256).astype(np.uint8)
    elif layer_type == 'binary_labels':
        plane = ((row + column) % 2).astype(np.uint8)
    elif layer_type == 'labels':
        plane = ((row * side + column) % 2048).astype(np.uint32)
    else:
        raise ValueError(f'Unknown raster layer type: {layer_type}')
    return np.stack((plane, np.flip(plane, axis=0)))


def _time_slice(viewer, app):
    viewer.dims.set_current_step(0, 1)
    app.processEvents()
    viewer.dims.set_current_step(0, 0)
    app.processEvents()


def _time_zoom(viewer, app):
    initial_zoom = viewer.scene.camera.zoom
    viewer.scene.camera.zoom = initial_zoom * 1.01
    app.processEvents()
    viewer.scene.camera.zoom = initial_zoom
    app.processEvents()


class QtEmptyGeometricLayerScalingSuite:
    """Render empty geometric layers while changing slice or camera."""

    params = (GEOMETRIC_LAYER_TYPES, LAYER_COUNTS)
    param_names = ['layer_type', 'n_layers']
    skip_params = Skip(always=_unsupported_geometric_partition)
    min_run_count = 3
    number = 1
    repeat = 3
    timeout = 300
    warmup_time = 0

    def setup(self, layer_type, n_layers):
        self.app = QApplication.instance() or QApplication([])
        self.viewer = napari.Viewer()
        self.viewer.add_image(
            np.zeros((2, GEOMETRIC_EXTENT, GEOMETRIC_EXTENT), dtype=np.uint8)
        )
        for _ in range(n_layers):
            _add_geometric_layer(self.viewer, layer_type)
        _time_slice(self.viewer, self.app)

    def teardown(self, *_):
        if hasattr(self, 'viewer'):
            self.viewer.close()

    def time_slice(self, *_):
        _time_slice(self.viewer, self.app)


class QtFixedGeometricLayerScalingSuite:
    """Render a fixed total primitive count split across layers."""

    params = (GEOMETRIC_LAYER_TYPES, LAYER_COUNTS)
    param_names = ['layer_type', 'n_layers']
    min_run_count = 3
    number = 1
    repeat = 3
    timeout = 300
    warmup_time = 0

    def setup(self, layer_type, n_layers):
        self.app = QApplication.instance() or QApplication([])
        self.viewer = napari.Viewer()
        self.viewer.add_image(
            np.zeros((2, GEOMETRIC_EXTENT, GEOMETRIC_EXTENT), dtype=np.uint8)
        )

        total_primitives = GEOMETRIC_TOTALS[layer_type]
        data = _geometric_data(layer_type, total_primitives)
        chunks = _split_geometric_data(data, n_layers)
        assert sum(len(chunk) for chunk in chunks) == total_primitives
        for chunk in chunks:
            _add_geometric_layer(self.viewer, layer_type, chunk)
        _time_zoom(self.viewer, self.app)

    def teardown(self, *_):
        if hasattr(self, 'viewer'):
            self.viewer.close()

    def time_zoom(self, *_):
        _time_zoom(self.viewer, self.app)


class QtFixedRasterLayerScalingSuite:
    """Render a fixed total displayed pixel count split across layers."""

    params = (RASTER_LAYER_TYPES, LAYER_COUNTS)
    param_names = ['layer_type', 'n_layers']
    skip_params = Skip(always=_unsupported_raster_partition)
    min_run_count = 3
    number = 1
    repeat = 3
    timeout = 300
    warmup_time = 0

    def setup(self, layer_type, n_layers):
        self.app = QApplication.instance() or QApplication([])
        self.viewer = napari.Viewer()

        grid_width = int(np.sqrt(n_layers))
        side = int(np.sqrt(TOTAL_RASTER_PIXELS // n_layers))
        data = _raster_data(layer_type, side)
        for index in range(n_layers):
            row, column = divmod(index, grid_width)
            if layer_type == 'image':
                self.viewer.add_image(
                    data, translate=(0, row * side, column * side)
                )
            else:
                self.viewer.add_labels(
                    data, translate=(0, row * side, column * side)
                )
        self.viewer.reset_view()
        extent = self.viewer.layers.extent.world[:, -2:]
        assert np.allclose(extent, ((0, 0), (RASTER_EXTENT - 1,) * 2))
        _time_slice(self.viewer, self.app)

    def teardown(self, *_):
        if hasattr(self, 'viewer'):
            self.viewer.close()

    def time_slice(self, *_):
        _time_slice(self.viewer, self.app)


if __name__ == '__main__':
    from utils import run_benchmark

    run_benchmark()
