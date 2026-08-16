import numpy as np
import pytest

from napari._vispy.layers.vectors import (
    VispyVectorsLayer,
    generate_vector_meshes,
    generate_vector_meshes_2D,
)
from napari._vispy.utils.qt_font import FontInfo
from napari._vispy.visuals.mesh import Mesh
from napari.components import Dims
from napari.layers import Vectors

VECTOR = np.array([[[0, 0], [1, 1]]])


def test_empty_vector_visual_is_hidden_until_data_is_added():
    layer = Vectors()
    vispy_layer = VispyVectorsLayer(layer, font_info=FontInfo())

    assert vispy_layer.node.visible
    assert not vispy_layer.node.mesh.visible

    layer.data = VECTOR
    assert vispy_layer.node.mesh.visible

    layer.visible = False
    assert not vispy_layer.node.visible

    layer.data = np.empty((0, 2, 2))
    layer.visible = True
    assert vispy_layer.node.visible
    assert not vispy_layer.node.mesh.visible


def test_vector_mesh_is_hidden_without_hiding_overlays(
    make_napari_viewer,
):
    vector = np.pad(VECTOR, ((0, 0), (0, 0), (1, 0)))
    viewer = make_napari_viewer()
    layer = viewer.add_vectors(vector)
    layer.bounding_box.visible = True
    canvas = viewer.window._qt_viewer.canvas
    vispy_layer = canvas.layer_to_visual[layer]
    bounding_box_visual = canvas._layer_overlay_to_visual[layer][
        layer.bounding_box
    ]

    assert vispy_layer.node.visible
    assert vispy_layer.node.mesh.visible
    assert bounding_box_visual.node.parent is vispy_layer.node
    assert bounding_box_visual.node.visible

    layer._slice_dims(Dims(ndim=3, point=(1, 0, 0)))
    assert vispy_layer.node.visible
    assert not vispy_layer.node.mesh.visible
    assert bounding_box_visual.node.visible

    layer._slice_dims(Dims(ndim=3, point=(0, 0, 0)))
    assert vispy_layer.node.mesh.visible


def test_repeated_vector_mesh_updates_reuse_rgba_color_transform():
    vectors = np.array([[[0, 0], [1, 1]]])
    layer = Vectors(vectors)
    vispy_layer = VispyVectorsLayer(layer, font_info=FontInfo())
    mesh = vispy_layer.node.mesh

    assert isinstance(mesh, Mesh)
    mesh._update_data()
    color_transform = mesh.shared_program.vert['color_transform']

    layer.edge_color = 'red'
    mesh._update_data()

    assert mesh.shared_program.vert['color_transform'] is color_transform


@pytest.mark.parametrize(
    ('edge_width', 'length', 'dims', 'style'),
    [
        (0, 0, 2, 'line'),
        (0.3, 0.3, 2, 'line'),
        (1, 1, 3, 'line'),
        (0, 0, 2, 'triangle'),
        (0.3, 0.3, 2, 'triangle'),
        (1, 1, 3, 'triangle'),
        (0, 0, 2, 'arrow'),
        (0.3, 0.3, 2, 'arrow'),
        (1, 1, 3, 'arrow'),
    ],
)
def test_generate_vector_meshes(edge_width, length, dims, style):
    n = 10

    data = np.random.random((n, 2, dims))
    vertices, faces = generate_vector_meshes(
        data, width=edge_width, length=length, vector_style=style
    )
    vertices_length, vertices_dims = vertices.shape
    faces_length, faces_dims = faces.shape

    if dims == 2:
        if style == 'line':
            assert vertices_length == 4 * n
            assert faces_length == 2 * n
        elif style == 'triangle':
            assert vertices_length == 3 * n
            assert faces_length == n
        elif style == 'arrow':
            assert vertices_length == 7 * n
            assert faces_length == 3 * n

    elif dims == 3:
        if style == 'line':
            assert vertices_length == 8 * n
            assert faces_length == 4 * n
        elif style == 'triangle':
            assert vertices_length == 6 * n
            assert faces_length == 2 * n
        elif style == 'arrow':
            assert vertices_length == 14 * n
            assert faces_length == 6 * n

    assert vertices_dims == dims
    assert faces_dims == 3


@pytest.mark.parametrize(
    ('edge_width', 'length', 'style', 'p'),
    [
        (0, 0, 'line', (1, 0, 0)),
        (0.3, 0.3, 'line', (0, 1, 0)),
        (1, 1, 'line', (0, 0, 1)),
        (0, 0, 'triangle', (1, 0, 0)),
        (0.3, 0.3, 'triangle', (0, 1, 0)),
        (1, 1, 'triangle', (0, 0, 1)),
        (0, 0, 'arrow', (1, 0, 0)),
        (0.3, 0.3, 'arrow', (0, 1, 0)),
        (1, 1, 'arrow', (0, 0, 1)),
    ],
)
def test_generate_vector_meshes_2D(edge_width, length, style, p):
    n = 10
    dims = 2

    data = np.random.random((n, 2, dims))
    vertices, faces = generate_vector_meshes_2D(
        data, width=edge_width, length=length, vector_style=style, p=p
    )
    vertices_length, vertices_dims = vertices.shape
    faces_length, faces_dims = faces.shape

    if style == 'line':
        assert vertices_length == 4 * n
        assert faces_length == 2 * n
    elif style == 'triangle':
        assert vertices_length == 3 * n
        assert faces_length == n
    elif style == 'arrow':
        assert vertices_length == 7 * n
        assert faces_length == 3 * n

    assert vertices_dims == dims
    assert faces_dims == 3


@pytest.mark.parametrize(
    ('initial_vector_style', 'new_vector_style'),
    [
        ('line', 'line'),
        ('line', 'triangle'),
        ('line', 'arrow'),
        ('triangle', 'line'),
        ('triangle', 'triangle'),
        ('triangle', 'arrow'),
        ('arrow', 'line'),
        ('arrow', 'triangle'),
        ('arrow', 'arrow'),
    ],
)
def test_vector_style_change(
    make_napari_viewer, initial_vector_style, new_vector_style
):
    # initialize viewer
    viewer = make_napari_viewer()
    # add a vector layer
    vector_layer = viewer.add_vectors(
        vector_style=initial_vector_style, name='vectors'
    )

    class Counter:
        def __init__(self):
            self.count = 0

        def increment_count(self, event):
            self.count += 1

    # initialize counter
    counter = Counter()
    # connect counter to vector_style change
    vector_layer.events.vector_style.connect(counter.increment_count)

    # change vector_style
    vector_layer.vector_style = new_vector_style

    # check if counter was called
    if initial_vector_style == new_vector_style:
        assert counter.count == 0
    else:
        assert counter.count == 1
