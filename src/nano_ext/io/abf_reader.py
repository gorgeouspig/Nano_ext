"""ABF (Axon Binary Format) file reader.

Reads electrophysiology data from ABF files.  The header is parsed with the
pyabf library; the sample data are memory-mapped directly from the file, so
only the requested channel is ever converted to floating point (pyabf itself
would convert every channel of the whole file to float32 up front).
Supports ABF1 and ABF2 formats commonly used with Axon/Molecular Devices
patch clamp amplifiers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from nano_ext.models import SignalData


def read_abf(
    filepath: str | Path,
    channel: int = 0,
    sweep: Optional[int] = None,
) -> SignalData:
    """Read a nanopore current trace from an ABF file.

    Parameters
    ----------
    filepath : str or Path
        Path to the ABF file.
    channel : int
        Channel number to read (0-indexed).
    sweep : int, optional
        Sweep number to read (0-indexed). If None, all sweeps are
        concatenated into a single continuous trace.

    Returns
    -------
    SignalData
        Loaded signal data with metadata.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ImportError
        If pyabf is not installed.
    ValueError
        If the channel or sweep number is out of range.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"ABF file not found: {filepath}")

    try:
        import pyabf
    except ImportError:
        raise ImportError(
            "pyabf is required to read ABF files. "
            "Install it with: pip install pyabf"
        )

    abf = pyabf.ABF(str(filepath), loadData=False)

    # Validate channel
    if channel >= abf.channelCount:
        raise ValueError(
            f"Channel {channel} out of range. "
            f"File has {abf.channelCount} channel(s) (0-{abf.channelCount - 1})."
        )

    sampling_rate = float(abf.dataRate)
    units = abf.adcUnits[channel] if channel < len(abf.adcUnits) else "pA"

    if sweep is not None and sweep >= abf.sweepCount:
        raise ValueError(
            f"Sweep {sweep} out of range. "
            f"File has {abf.sweepCount} sweep(s) (0-{abf.sweepCount - 1})."
        )

    raw = abf_raw_channel(abf, channel)
    if sweep is not None:
        start, end = _sweep_bounds(abf, sweep)
        raw = raw[start:end]
    else:
        # All sweeps concatenated (a contiguous block for every ABF layout).
        start, _ = _sweep_bounds(abf, 0)
        _, end = _sweep_bounds(abf, abf.sweepCount - 1)
        raw = raw[start:end]
    gain, offset = abf_channel_scaling(abf, channel)
    signal = _scale(raw, gain, offset)

    metadata = {
        "format": "abf",
        "abf_version": abf.abfVersion.get("major", 0),
        "filepath": str(filepath),
        "n_channels": abf.channelCount,
        "n_sweeps": abf.sweepCount,
        "sweep": sweep,
        "protocol": getattr(abf, "protocol", ""),
        "abf_id": abf.abfID,
        "data_format": "int16" if abf._nDataFormat == 0 else "float32",
        "scale_gain": gain,
        "scale_offset": offset,
    }

    return SignalData(
        signal=signal,
        sampling_rate=sampling_rate,
        channel=channel,
        units=units,
        metadata=metadata,
    )


def abf_raw_channel(abf, channel: int) -> np.ndarray:
    """Memory-mapped raw samples (int16 or float32) of one ABF channel.

    The returned array is a strided view into the file: nothing is read until
    it is indexed, so slicing it (e.g. for display) touches only that part of
    the file.  Convert to physical units with :func:`abf_channel_scaling`.
    """
    dtype = np.int16 if abf._nDataFormat == 0 else np.float32
    n_channels = abf.channelCount
    n_points = int(abf.dataPointCount) // n_channels
    data = np.memmap(
        abf.abfFilePath,
        dtype=dtype,
        mode="r",
        offset=int(abf.dataByteStart),
        shape=(n_points, n_channels),
    )
    return data[:, channel]


def abf_channel_scaling(abf, channel: int) -> tuple[float, float]:
    """``(gain, offset)`` converting raw samples to physical units.

    Identity for float32 files; the ADC scaling from the header for int16
    files (the same factors pyabf applies).
    """
    if abf._nDataFormat != 0:
        return 1.0, 0.0
    return float(abf._dataGain[channel]), float(abf._dataOffset[channel])


def _scale(raw: np.ndarray, gain: float, offset: float) -> np.ndarray:
    """Raw samples -> float32 physical units, in one pass over the data."""
    out = np.empty(len(raw), dtype=np.float32)
    # Chunked to avoid a float64 temporary of the whole trace.
    chunk = 1 << 22
    for i in range(0, len(raw), chunk):
        block = np.asarray(raw[i:i + chunk], dtype=np.float32)
        if gain != 1.0 or offset != 0.0:
            block = block * np.float32(gain) + np.float32(offset)
        out[i:i + chunk] = block
    return out


def _sweep_bounds(abf, sweep: int) -> tuple[int, int]:
    """Point range ``[start, end)`` of a sweep (same logic as pyabf.setSweep)."""
    fixed = True
    if abf.sweepCount > 1 and hasattr(abf, "_synchArraySection"):
        fixed = len(set(abf._synchArraySection.lLength)) == 1
    if fixed:
        start = abf.sweepPointCount * sweep
        return start, start + abf.sweepPointCount
    lengths = [n // abf.channelCount for n in abf._synchArraySection.lLength]
    start = sum(lengths[:sweep])
    return start, start + lengths[sweep]


def get_abf_info(filepath: str | Path) -> dict:
    """Get summary information about an ABF file without loading all data.

    Parameters
    ----------
    filepath : str or Path
        Path to the ABF file.

    Returns
    -------
    dict
        Dictionary with file information.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"ABF file not found: {filepath}")

    import pyabf

    abf = pyabf.ABF(str(filepath), loadData=False)

    return {
        "filepath": str(filepath),
        "abf_id": abf.abfID,
        "abf_version": abf.abfVersion,
        "n_channels": abf.channelCount,
        "n_sweeps": abf.sweepCount,
        "sampling_rate_hz": abf.dataRate,
        # From the data size: abf.dataLengthSec can disagree for multi-sweep ABF1 files.
        "duration_sec": abf.dataPointCount / abf.channelCount / abf.dataRate,
        "n_samples_per_sweep": abf.sweepPointCount,
        "channel_units": abf.adcUnits,
        "channel_names": abf.adcNames,
        "protocol": getattr(abf, "protocol", ""),
    }
