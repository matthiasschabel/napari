"""Experimental scientific model with optional consumers imported on demand."""

import lazy_loader as lazy

__getattr__, __dir__, __all__ = lazy.attach(
    __name__,
    submod_attrs={
        '_annotation': ['ROI', 'PlaneAnchor', 'ROICollection'],
        '_axis': [
            'AxisDtype',
            'AxisRole',
            'CoordinateAxis',
            'CoordinateFrame',
            'FrameAxis',
        ],
        '_basis_conversion': ['convert_field_basis'],
        '_data_object': ['DataObject'],
        '_derived': ['DerivedSource'],
        '_domain': ['Domain', 'StructuredGridDomain'],
        '_field': ['Field', 'Interpolation'],
        '_field_geometry': ['FieldGeometry'],
        '_geometry': ['Polygon', 'PolygonSetDomain'],
        '_graph': [
            'CoordinateGraph',
            'CoordinateTransform',
            'CorrespondenceBasis',
            'FrameRegistry',
            'MappingEdge',
            'ResolvedPath',
            'Unplaced',
        ],
        '_image_binding': ['ImageBinding'],
        '_layer_adapters': [
            'data_object_from_layer',
            'layer_from_data_object',
        ],
        '_level_geometry': ['LevelGeometry'],
        '_mapping': ['AffineMapping', 'CoordinateEmbedding'],
        '_measure': ['mean_over_roi'],
        '_mesh_domain': ['MeshDomain'],
        '_mpr': ['MPRController'],
        '_mri': ['synthetic_mri'],
        '_selection': ['CoordinateSelection'],
        '_shapes_codec': [
            'polygon_to_shape_vertices',
            'shape_vertices_to_polygon',
        ],
        '_source': [
            'ArraySource',
            'DataSource',
            'MultiscaleSource',
            'SourceChangedError',
            'source_level_geometry',
            'source_revision',
        ],
        '_view_bridge': ['add_to_viewer'],
        '_xarray_adapter': [
            'XarrayEmbedding',
            'data_object_from_xarray',
            'embedding_from_reference_frame',
        ],
    },
)
