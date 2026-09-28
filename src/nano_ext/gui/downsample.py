"""View-dependent downsampling of long traces for display.

A 10-minute, 250 kHz recording has 150 M samples; a browser can draw a few
thousand points per trace smoothly.  For every zoom level only the visible
window is reduced, with MinMaxLTTB (keeps local extrema, so brief blockades
stay visible) from the ``tsdownsample`` package when installed, otherwise a
NumPy min/max envelope.

Traces are addressed through :class:`TraceIndex`, which converts between
original-file time and the sample indices of an analysed signal (which may be
cropped or a concatenation of several ranges).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from nano_ext.models import SignalData

try:  # optional, much faster
    from tsdownsample import MinMaxLTTBDownsampler as _MinMaxLTTB
except Exception:  # pragma: no cover - exercised when not installed
    _MinMaxLTTB = None


def minmax_indices(y: np.ndarray, n_out: int) -> np.ndarray:
    """Indices (sorted) of a shape-preserving subset of *y* with ≤ ~n_out points."""
    n = len(y)
    if n <= n_out:
        return np.arange(n)
    if _MinMaxLTTB is not None and n_out >= 4:
        y_c = np.ascontiguousarray(y)
        if y_c.dtype not in (np.float32, np.float64):
            y_c = y_c.astype(np.float32)
        if np.isnan(y_c.min()):  # min() propagates NaN; one pass, no temporary
            return _envelope_indices(y_c, n_out)
        return np.asarray(_MinMaxLTTB().downsample(y_c, n_out=n_out, parallel=n > 1_000_000),
                          dtype=np.int64)
    return _envelope_indices(y, n_out)


def _envelope_indices(y: np.ndarray, n_out: int) -> np.ndarray:
    """Min and max index of each of n_out/2 equal bins (NumPy fallback)."""
    n = len(y)
    n_bins = max(1, n_out // 2)
    edges = np.linspace(0, n, n_bins + 1).astype(np.int64)
    out = np.empty(2 * n_bins, dtype=np.int64)
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        if hi <= lo:
            out[2 * b] = out[2 * b + 1] = min(lo, n - 1)
            continue
        seg = y[lo:hi]
        i_min = lo + int(np.nanargmin(seg)) if np.isfinite(seg).any() else lo
        i_max = lo + int(np.nanargmax(seg)) if np.isfinite(seg).any() else lo
        out[2 * b], out[2 * b + 1] = sorted((i_min, i_max))
    return np.unique(out)


class TraceIndex:
    """Time <-> index mapping for arrays aligned with a :class:`SignalData`."""

    def __init__(self, signal_data: SignalData):
        self.sd = signal_data
        self.sr = signal_data.sampling_rate
        self.n = len(signal_data.signal)

    @property
    def t_start(self) -> float:
        return float(self.sd.time_at([0])[0]) if self.n else 0.0

    @property
    def t_end(self) -> float:
        return float(self.sd.time_at([self.n - 1])[0]) + 1.0 / self.sr if self.n else 0.0

    def window(self, t0: Optional[float], t1: Optional[float]) -> tuple[int, int]:
        """Index range of samples inside ``[t0, t1)`` (None = open end)."""
        t0 = self.t_start if t0 is None else t0
        t1 = self.t_end if t1 is None else t1
        return self.sd.index_range(t0, t1)

    def view(
        self,
        arrays: dict[str, np.ndarray],
        t0: Optional[float] = None,
        t1: Optional[float] = None,
        n_out: int = 4000,
        key: Optional[str] = None,
    ) -> dict:
        """Downsampled ``{"t": ..., name: ...}`` for the visible window.

        Point selection is driven by ``arrays[key]`` (default: the first
        array); the other arrays are sampled at the same indices.  NaN
        separators are inserted where consecutive samples are not
        contiguous in the original file (excluded ranges), so lines are not
        drawn across gaps.
        """
        i0, i1 = self.window(t0, t1)
        names = list(arrays)
        key = key or names[0]
        if i1 <= i0:
            return {"t": np.empty(0), **{k: np.empty(0) for k in names}, "n_raw": 0}
        idx = i0 + minmax_indices(arrays[key][i0:i1], n_out)
        t = self.sd.time_at(idx)
        out = {k: np.asarray(arrays[k][idx], dtype=np.float64) for k in names}

        if self.sd.index_map is not None and len(t) > 1:
            # Real discontinuities: original indices jump by more than the
            # sample stride would allow.
            orig = self.sd.index_map[idx]
            local = np.diff(idx)
            gaps = np.flatnonzero(np.diff(orig) > local)
        else:
            gaps = np.empty(0, dtype=np.int64)
        if len(gaps):
            t = np.insert(t, gaps + 1, np.nan)
            for k in names:
                out[k] = np.insert(out[k], gaps + 1, np.nan)
        return {"t": t, **out, "n_raw": int(i1 - i0)}
