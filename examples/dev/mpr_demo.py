"""Display a synthetic MRI volume in three linked orthogonal panes.

The embedded Qt viewers read napari settings transitively. This demo does not
mutate or reset those settings.
"""

import napari
from napari.experimental._data_model import MPRController, synthetic_mri
from napari.experimental._data_model._mpr_qt import MPRWidget
from napari.qt import get_qapp

get_qapp()
controller = MPRController(synthetic_mri())
widget = MPRWidget(controller)
widget.resize(1500, 600)


if __name__ == '__main__':
    widget.show()
    napari.run()
elif __name__ == '<run_path>':
    # runpy.run_path uses this sentinel in the example smoke test.
    from qtpy.QtCore import QCoreApplication, QEvent

    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
