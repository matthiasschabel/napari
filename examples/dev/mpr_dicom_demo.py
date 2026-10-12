"""Display a real multi-parametric DICOM acquisition in linked MPR panes.

Run from the repository root with pirana's local packages available::

    PYTHONPATH=$PWD/src:$HOME/GitHub/pirana-lab/packages/pirana-dicom/src:$HOME/GitHub/pirana-lab/packages/pirana-core/src python examples/dev/mpr_dicom_demo.py

Set ``PIRANA_MPR_ARCHIVE`` to the archive-unit directory when the walkthrough
study is stored elsewhere. ``PIRANA_MPR_RECIPE`` selects an integer recipe and
defaults to recipe 9, the 98-slice axial multi-echo GRE acquisition.
Recipe 22 provides a 65-time-point ``80 x 256 x 160`` temporal stress case::

    PIRANA_MPR_RECIPE=22 PYTHONPATH=$PWD/src:$HOME/GitHub/pirana-lab/packages/pirana-dicom/src:$HOME/GitHub/pirana-lab/packages/pirana-core/src python examples/dev/mpr_dicom_demo.py

The demo adapts pirana's magnitude data directly; it does not materialize or
retain complex components merely to display magnitude.
"""

import os
import sys
from pathlib import Path

_DEFAULT_ARCHIVE = (
    '/Volumes/RobertsLab/archive/1.2.40.0.13.1.3/'
    '2025-04-29-38698_20250429_38698_20250429-0900-'
    '1.3.12.2.1107.5.2.43.167043.30000025042516541035700000010'
)
_DEFAULT_RECIPE = 9

widget = None

try:
    import pirana.dicom as pirana_dicom
except ImportError:
    print('mpr_dicom_demo: pirana.dicom is unavailable; skipping.')
else:
    archive = Path(
        os.environ.get('PIRANA_MPR_ARCHIVE', _DEFAULT_ARCHIVE)
    ).expanduser()
    if not archive.is_dir():
        print(f'mpr_dicom_demo: archive not found at {archive}; skipping.')
    else:
        import numpy as np

        import napari
        from napari.experimental._data_model import (
            ROI,
            DataObject,
            FrameRegistry,
            MPRController,
            PlaneAnchor,
            Polygon,
            PolygonSetDomain,
            ROICollection,
            data_object_from_xarray,
            embedding_from_reference_frame,
            mean_over_roi,
        )
        from napari.experimental._data_model._mpr_qt import MPRWidget
        from napari.experimental._data_model._view_bridge import (
            _embedding_coordinates,
        )
        from napari.qt import get_qapp

        recipes_path = archive / f'{archive.name}.recipes.json'
        recipe_text = os.environ.get('PIRANA_MPR_RECIPE', str(_DEFAULT_RECIPE))
        try:
            recipe = int(recipe_text)
        except ValueError as exc:
            raise ValueError('PIRANA_MPR_RECIPE must be an integer') from exc
        try:
            dicom_data = pirana_dicom.Recipes.load(recipes_path).to_data(
                recipe, archive=archive
            )
        except ImportError:
            print(
                'mpr_dicom_demo: pirana archive support is unavailable; '
                'skipping.'
            )
        else:
            magnitude = dicom_data.signal_magnitude().rename('magnitude')
            known_axis_units = {
                'TimePoint': None,
                'EchoTime': 'ms',
                'FlipAngle': 'deg',
                'z': 'mm',
                'y': 'mm',
                'x': 'mm',
            }
            unknown_dims = [
                name for name in magnitude.dims if name not in known_axis_units
            ]
            if unknown_dims:
                print(
                    'mpr_dicom_demo: no unit known for '
                    f'{unknown_dims}; those axes stay unitless.',
                    file=sys.stderr,
                )
            frame_registry = FrameRegistry()
            data_object = data_object_from_xarray(
                magnitude,
                name=f'DICOM recipe {recipe} magnitude',
                units={
                    name: known_axis_units[name]
                    for name in magnitude.dims
                    if name in known_axis_units
                },
                embedding=embedding_from_reference_frame(
                    magnitude.dims,
                    dicom_data.reference_frame,
                    registry=frame_registry,
                ),
            )

            embedding = data_object.embeddings[0]
            target_axis_by_name = {
                axis.name: index
                for index, axis in enumerate(embedding.target_frame.axes)
            }
            if set(target_axis_by_name) != {'L', 'P', 'S'}:
                raise ValueError('mpr_dicom_demo requires an LPS target frame')
            spatial_domain_axes, _, _, _ = _embedding_coordinates(embedding)
            spatial_domain_axis_by_name = {
                name: spatial_domain_axes[target_axis]
                for name, target_axis in target_axis_by_name.items()
            }

            def world_values(name):
                target_axis = target_axis_by_name[name]
                domain_axis = spatial_domain_axis_by_name[name]
                return embedding.offset[target_axis] + embedding.matrix[
                    target_axis, domain_axis
                ] * np.arange(data_object.domain.axes[domain_axis].size)

            frame_axes = {
                axis.name: axis for axis in embedding.target_frame.axes
            }
            spatial_domain_axes = set(spatial_domain_axis_by_name.values())
            context = {}
            for index, axis in enumerate(data_object.domain.axes):
                if index in spatial_domain_axes or axis.name == 'EchoTime':
                    continue
                middle = (axis.size - 1) // 2
                if axis.categorical_labels is not None:
                    value = axis.categorical_labels[middle]
                elif axis.values is not None:
                    value = axis.values[middle]
                    if isinstance(value, np.generic):
                        value = value.item()
                else:
                    value = float(middle)
                context[axis.name] = value

            def _organic_ring(center, radii, phase, vertices):
                angles = np.linspace(0, 2 * np.pi, vertices, endpoint=False)
                radial = (
                    1
                    + 0.10 * np.sin(3 * angles + phase)
                    + 0.06 * np.cos(5 * angles - 0.5 * phase)
                )
                return np.column_stack(
                    (
                        center[0] + radii[0] * radial * np.cos(angles),
                        center[1] + radii[1] * radial * np.sin(angles),
                    )
                )

            def _organic_roi(name, plane_axis, in_plane_axes, phase):
                in_plane_values = tuple(
                    world_values(axis_name) for axis_name in in_plane_axes
                )
                center = np.array(
                    [
                        values[(len(values) - 1) // 2]
                        for values in in_plane_values
                    ]
                )
                radii = np.array(
                    [np.ptp(values) * 0.125 for values in in_plane_values]
                )
                first_hole_center = center + radii * np.array([0.30, -0.22])
                second_hole_center = center + radii * np.array([-0.32, 0.28])
                first_hole_radii = radii * np.array([0.25, 0.22])
                second_hole_radii = radii * np.array([0.20, 0.24])
                exterior = _organic_ring(center, radii, phase, 40)
                first_hole = _organic_ring(
                    first_hole_center, first_hole_radii, phase + 0.7, 18
                )
                second_hole = _organic_ring(
                    second_hole_center, second_hole_radii, phase + 1.4, 16
                )
                island = _organic_ring(
                    first_hole_center,
                    first_hole_radii * 0.38,
                    phase + 2.1,
                    14,
                )
                domain = PolygonSetDomain(
                    (
                        Polygon(exterior, (first_hole, second_hole)),
                        Polygon(island),
                    ),
                    in_plane_axes,
                    tuple(frame_axes[axis].unit for axis in in_plane_axes),
                )
                plane_values = world_values(plane_axis)
                plane_value = plane_values[(len(plane_values) - 1) // 2]
                anchor = PlaneAnchor(
                    embedding.target_frame,
                    plane_axis,
                    plane_value,
                    in_plane_axes,
                    context,
                )
                return ROI(
                    name,
                    DataObject(f'{name} geometry', domain),
                    anchor,
                    data_object,
                )

            axial_roi = _organic_roi(
                'mid axial organic ROI', 'S', ('P', 'L'), 0.0
            )
            coronal_roi = _organic_roi(
                'mid coronal organic ROI', 'P', ('S', 'L'), 0.8
            )
            sagittal_roi = _organic_roi(
                'mid sagittal organic ROI', 'L', ('S', 'P'), 1.6
            )
            roi_collection = ROICollection(data_object)
            for roi in (axial_roi, coronal_roi, sagittal_roi):
                roi_collection.add(roi)

            if 'EchoTime' in {axis.name for axis in data_object.domain.axes}:
                echo_means = mean_over_roi(
                    data_object,
                    axial_roi,
                    'magnitude',
                    sample_axes=('EchoTime',),
                )
                print('Mean magnitude over the pre-seeded ROI:')
                for echo_time, mean in echo_means.items():
                    print(f'  EchoTime={echo_time}: {mean:.6g}')

            get_qapp()
            controller = MPRController(
                data_object,
                'magnitude',
                roi_collection=roi_collection,
            )
            controller.begin_roi_draw(0)
            widget = MPRWidget(controller)
            widget.resize(1500, 600)
            print('Draw polygons in the axial roi-draw layer to add ROIs.')


if widget is not None:
    if __name__ == '__main__':
        widget.show()
        napari.run()
    elif __name__ == '<run_path>':
        # runpy.run_path uses this sentinel in the example smoke test.
        from qtpy.QtCore import QCoreApplication, QEvent

        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
