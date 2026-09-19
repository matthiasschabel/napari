import logging

import numpy as np
from vispy import use
from vispy.gloo import VertexBuffer
from vispy.scene.visuals import Markers as BaseMarkers

logger = logging.getLogger(__name__)

try:
    use(gl='gl+')
except RuntimeError:
    logger.warning(
        'Could not use gl+ for instanced rendering of points.'
        'Falling back to GL point rendering.'
    )
    rendering_method = 'points'
else:
    rendering_method = 'instanced'


class Markers(BaseMarkers):
    def __init__(self, *args, method=rendering_method, **kwargs) -> None:
        self._full_data = None
        self._full_vbo = None
        self._view_vbo = None
        self._view_data = None
        self._view_capacity = 0
        self._view_count = 0
        super().__init__(*args, method=method, **kwargs)
        if self._full_vbo is None:
            self._full_vbo = self._vbo

    def set_data(self, *args, **kwargs) -> None:
        if self._full_vbo is not None:
            self._vbo = self._full_vbo
        super().set_data(*args, **kwargs)
        if self._full_vbo is None:
            self._full_vbo = self._vbo
        self._full_data = self._data
        self._view_vbo = None
        self._view_data = None
        self._view_capacity = 0
        self._view_count = 0

    def set_view_indices(self, indices=None, *, capacity=None) -> None:
        """Upload a subset of the retained marker payload.

        Parameters
        ----------
        indices : array-like or None
            Indices into the full marker payload. ``None`` restores it.
        capacity : int, optional
            Maximum subset size. A fixed-capacity buffer avoids reallocating
            GPU storage as the viewport moves.
        """
        if self._full_data is None:
            self._data = None
        elif indices is None:
            self._data = self._full_data
            self._bind_vbo(self._full_vbo)
        else:
            count = len(indices)
            capacity = max(count, capacity or count, 1)
            if self._view_vbo is None or capacity != self._view_capacity:
                self._view_data = np.zeros(
                    capacity, dtype=self._full_data.dtype
                )
                self._view_vbo = VertexBuffer(self._view_data)
                self._view_capacity = capacity
                self._view_count = 0
            if count:
                np.take(
                    self._full_data,
                    indices,
                    axis=0,
                    out=self._view_data[:count],
                )
                self._data = self._view_data[:count]
                self._view_vbo.set_subdata(self._data)
                self._bind_vbo(self._view_vbo, count=count)
            else:
                self._data = None
            self._view_count = count

        self.events.data_updated()
        self.update()

    def _bind_vbo(self, vbo: VertexBuffer, *, count=None) -> None:
        self._vbo = vbo
        divisor = 1 if self._method == 'instanced' else None
        for name in self._full_data.dtype.names:
            view = vbo[name]
            if count is not None:
                # Program.draw reads DataBufferView._size, and vispy exposes no
                # public way to bind only the populated prefix of a field view.
                view._size = count
                view._nbytes = count * view._itemsize
            view.divisor = divisor
            self.shared_program[name] = view

    def _compute_bounds(self, axis, view):
        # needed for entering 3D rendering mode when a points
        # layer is invisible and the self._data property is None
        if self._full_data is None:
            return None
        pos = self._full_data['a_position']
        if pos is None or pos.size == 0:
            return None
        if pos.shape[1] > axis:
            return (pos[:, axis].min(), pos[:, axis].max())

        return (0, 0)
