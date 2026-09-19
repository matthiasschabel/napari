from __future__ import annotations

import math
from threading import Lock
from typing import TYPE_CHECKING

import numpy as np

from napari._vispy.utils.text import _has_visible_text
from napari.layers.points._points_constants import Mode

if TYPE_CHECKING:
    from napari._qt.qthreading import FunctionWorker
    from napari._vispy.visuals.markers import Markers
    from napari.layers import Points


MIN_POINTS = 5_000_000
MAX_VIEWPORT_AREA_RATIO = 0.0015
MAX_PAYLOAD_RATIO = 0.0075
GUARD_FRACTION = 0.5
DEBOUNCE_FRAMES = 2
PER_LAYER_BUDGET = 256 * 1024**2
TOTAL_BUDGET = 512 * 1024**2


class _IndexBudget:
    """Bound the persistent memory added by optional spatial indexes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._reserved = 0

    def reserve(self, amount: int) -> bool:
        with self._lock:
            if self._reserved + amount > TOTAL_BUDGET:
                return False
            self._reserved += amount
            return True

    def release(self, amount: int) -> None:
        with self._lock:
            self._reserved -= amount


_INDEX_BUDGET = _IndexBudget()


def _estimate_index_bytes(data: np.ndarray) -> int:
    bytes_per_point = (
        24 if data.dtype == np.float64 and data.flags.c_contiguous else 40
    )
    return len(data) * bytes_per_point


def _build_index(data: np.ndarray, generation: int):
    from scipy.spatial import cKDTree

    if not np.isfinite(data).all():
        return generation, None
    try:
        tree = cKDTree(
            data,
            leafsize=32,
            compact_nodes=False,
            copy_data=False,
            balanced_tree=False,
        )
    except (MemoryError, ValueError):
        tree = None
    return generation, tree


class PointsViewportCuller:
    """Select the packed marker payload needed for a 2D viewport."""

    def __init__(self, layer: Points, markers: Markers) -> None:
        self._layer = layer
        self._markers = markers
        self._tree = None
        self._worker: FunctionWorker | None = None
        self._generation = 0
        self._tree_generation = -1
        self._stable_frames = 0
        self._building_generation = -1
        self._failed_generation = -1
        self._reserved_bytes = 0
        self._view_indices: np.ndarray | None = None
        self._payload_bounds: tuple[np.ndarray, np.ndarray] | None = None
        self._rejected_bounds: tuple[np.ndarray, np.ndarray] | None = None
        self._all_shown = False
        self._max_marker_size = 0.0
        self._max_edge_width = 0.0
        self._closed = False

    @property
    def requires_viewbox_update(self) -> bool:
        return self._layer.ndim == 2 and len(self._layer.data) >= MIN_POINTS

    def coordinates_changed(self) -> None:
        self._generation += 1
        self._tree = None
        self._tree_generation = -1
        self._stable_frames = 0
        self._failed_generation = -1
        self._payload_bounds = None
        self._rejected_bounds = None
        self._all_shown = bool(np.all(self._layer.shown))
        self.restore_full_view()
        if self._worker is None:
            self._release_budget()

    def marker_payload_changed(self) -> None:
        # Markers.set_data restores the full payload without changing coordinates.
        self._view_indices = None
        self._payload_bounds = None
        self._rejected_bounds = None
        self._all_shown = bool(np.all(self._layer.shown))
        payload = self._markers._full_data
        if payload is None or not len(payload):
            self._max_marker_size = 0.0
            self._max_edge_width = 0.0
        else:
            self._max_marker_size = float(np.max(payload['a_size']))
            self._max_edge_width = float(np.max(payload['a_edgewidth']))

    def restore_full_view(self, *, clear_rejection: bool = True) -> None:
        if self._view_indices is not None:
            self._markers.set_view_indices()
            self._view_indices = None
        self._payload_bounds = None
        if clear_rejection:
            self._rejected_bounds = None

    def padding_changed(self) -> None:
        self._payload_bounds = None
        self._rejected_bounds = None

    def prepare(
        self,
        world_corners: np.ndarray,
        canvas_size: np.ndarray,
    ) -> None:
        if not self._runtime_eligible(world_corners, canvas_size):
            self.restore_full_view()
            return

        bounds = self._data_view_bounds(world_corners, canvas_size)
        if bounds is None:
            self.restore_full_view()
            return
        lower, upper, padding = bounds
        if self._tree is None:
            extent = np.ptp(self._layer.extent.data[:, (0, 1)], axis=0)
        else:
            extent = self._tree.maxes - self._tree.mins
        viewport_size = upper - lower
        if (
            not np.isfinite(extent).all()
            or np.any(extent <= 0)
            or np.prod(viewport_size) / np.prod(extent)
            > MAX_VIEWPORT_AREA_RATIO
        ):
            self._stable_frames = 0
            self.restore_full_view()
            return

        if self._tree is None or self._tree_generation != self._generation:
            self.restore_full_view()
            if self._failed_generation == self._generation:
                return
            self._stable_frames += 1
            if self._stable_frames >= DEBOUNCE_FRAMES:
                self._start_index_build()
            return

        required_lower = lower - padding
        required_upper = upper + padding
        if self._rejected_bounds is not None:
            rejected_lower, rejected_upper = self._rejected_bounds
            if np.all(required_lower >= rejected_lower) and np.all(
                required_upper <= rejected_upper
            ):
                self.restore_full_view(clear_rejection=False)
                return
            self._rejected_bounds = None
        if self._payload_bounds is not None and self._view_indices is not None:
            payload_lower, payload_upper = self._payload_bounds
            if np.all(required_lower >= payload_lower) and np.all(
                required_upper <= payload_upper
            ):
                return

        guard = viewport_size * GUARD_FRACTION
        lower = required_lower - guard
        upper = required_upper + guard
        if np.any(upper <= lower):
            self.restore_full_view()
            return

        center = (lower + upper) / 2
        radius = np.linalg.norm((upper - lower) / 2)
        candidates = np.asarray(
            self._tree.query_ball_point(center, radius), dtype=np.intp
        )
        if len(candidates):
            points = self._tree.data[candidates]
            inside = np.all((points >= lower) & (points <= upper), axis=1)
            indices = np.sort(candidates[inside])
        else:
            indices = candidates

        if len(indices) / len(self._layer.data) > MAX_PAYLOAD_RATIO:
            self.restore_full_view()
            self._rejected_bounds = (lower, upper)
            return

        capacity = math.ceil(len(self._layer.data) * MAX_PAYLOAD_RATIO)
        self._markers.set_view_indices(indices, capacity=capacity)
        self._view_indices = indices
        self._payload_bounds = (lower, upper)

    def _runtime_eligible(
        self, world_corners: np.ndarray, canvas_size: np.ndarray
    ) -> bool:
        layer = self._layer
        if (
            not self.requires_viewbox_update
            or not layer.visible
            or layer.mode != Mode.PAN_ZOOM
            or layer._slice_input.ndisplay != 2
            or tuple(layer._slice_input.displayed) != (0, 1)
            or not self._all_shown
            or _has_visible_text(layer)
            or world_corners.shape != (2, 2)
            or canvas_size.shape != (2,)
            or not np.isfinite(world_corners).all()
            or not np.isfinite(canvas_size).all()
            or np.any(canvas_size <= 0)
        ):
            return False

        transform = layer._transforms[1:].simplified.set_slice((0, 1))
        diagonal = np.diag(transform.linear_matrix)
        return (
            transform._is_diagonal
            and np.isfinite(diagonal).all()
            and np.all(diagonal != 0)
        )

    def _data_view_bounds(
        self, world_corners: np.ndarray, canvas_size: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        layer = self._layer
        transform = layer._transforms[1:].simplified.set_slice((0, 1))
        data_corners = transform.inverse(world_corners)
        lower = np.min(data_corners, axis=0)
        upper = np.max(data_corners, axis=0)

        if (
            self._max_marker_size <= 0
            or not np.isfinite(
                [self._max_marker_size, self._max_edge_width]
            ).all()
        ):
            return None
        diagonal = np.abs(np.diag(transform.linear_matrix))
        world_per_pixel = (
            np.abs(world_corners[1] - world_corners[0]) / canvas_size
        )
        if np.any(world_per_pixel <= 0):
            return None

        marker_pixel_scale = 1 / world_per_pixel[-1]
        marker_size = self._max_marker_size * marker_pixel_scale
        edge_width = self._max_edge_width * marker_pixel_scale
        min_size, max_size = self._markers.canvas_size_limits
        clamped_size = marker_size
        if min_size >= 0:
            clamped_size = max(clamped_size, min_size)
        if max_size >= 0:
            clamped_size = min(clamped_size, max_size)
        if clamped_size != marker_size:
            edge_width *= clamped_size / marker_size
            if min_size >= 0:
                edge_width = max(edge_width, min_size * 0.5)
            edge_width = min(edge_width, clamped_size * 0.5)

        diameter_pixels = clamped_size + 4 * (
            edge_width + 1.5 * self._markers.antialias
        )
        if min_size >= 0:
            min_diameter = min_size + 4 * (
                min_size * 0.5 + 1.5 * self._markers.antialias
            )
            diameter_pixels = max(diameter_pixels, min_diameter)
        if not np.isfinite(diameter_pixels):
            return None
        padding = diameter_pixels * world_per_pixel / (2 * diagonal)

        return lower, upper, padding

    def _start_index_build(self) -> None:
        from napari._qt.qthreading import create_worker

        if self._worker is not None or self._closed:
            return
        data = self._layer.data
        estimate = _estimate_index_bytes(data)
        if estimate > PER_LAYER_BUDGET:
            return
        if self._reserved_bytes == 0:
            if not _INDEX_BUDGET.reserve(estimate):
                return
            self._reserved_bytes = estimate

        generation = self._generation
        self._building_generation = generation
        self._stable_frames = 0
        worker = create_worker(
            _build_index,
            data,
            generation,
            _ignore_errors=True,
        )
        worker.returned.connect(self._on_index_ready)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_index_ready(self, result) -> None:
        generation, tree = result
        if self._closed or generation != self._generation:
            return
        if tree is None:
            self._failed_generation = generation
            return
        self._tree = tree
        self._tree_generation = generation
        self._payload_bounds = None
        self._markers.update()

    def _on_worker_finished(self) -> None:
        building_generation = self._building_generation
        self._building_generation = -1
        self._worker = None
        if self._tree is None:
            self._release_budget()
            if (
                not self._closed
                and building_generation != self._generation
                and self.requires_viewbox_update
            ):
                self._markers.update()

    def _release_budget(self) -> None:
        if self._reserved_bytes:
            _INDEX_BUDGET.release(self._reserved_bytes)
            self._reserved_bytes = 0

    def close(self) -> None:
        self._closed = True
        self._generation += 1
        self.restore_full_view()
        self._tree = None
        if self._worker is not None:
            self._worker.quit()
        else:
            self._release_budget()
