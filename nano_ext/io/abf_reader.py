"""ABF (Axon Binary Format) file reader.

Reads electrophysiology data from ABF files using the pyabf library.
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

    abf = pyabf.ABF(str(filepath))

    # Validate channel
    if channel >= abf.channelCount:
        raise ValueError(
            f"Channel {channel} out of range. "
            f"File has {abf.channelCount} channel(s) (0-{abf.channelCount - 1})."
        )

    sampling_rate = float(abf.dataRate)
    units = abf.adcUnits[channel] if channel < len(abf.adcUnits) else "pA"

    if sweep is not None:
        # Read a single sweep
        if sweep >= abf.sweepCount:
            raise ValueError(
                f"Sweep {sweep} out of range. "
                f"File has {abf.sweepCount} sweep(s) (0-{abf.sweepCount - 1})."
            )
        abf.setSweep(sweepNumber=sweep, channel=channel)
        signal = np.array(abf.sweepY, dtype=np.float64)
    else:
        # Concatenate all sweeps
        traces = []
        for sw in range(abf.sweepCount):
            abf.setSweep(sweepNumber=sw, channel=channel)
            traces.append(np.array(abf.sweepY, dtype=np.float64))
        signal = np.concatenate(traces)

    metadata = {
        "format": "abf",
        "abf_version": abf.abfVersion.get("major", 0),
        "filepath": str(filepath),
        "n_channels": abf.channelCount,
        "n_sweeps": abf.sweepCount,
        "sweep": sweep,
        "protocol": getattr(abf, "protocol", ""),
        "abf_id": abf.abfID,
    }

    return SignalData(
        signal=signal,
        sampling_rate=sampling_rate,
        channel=channel,
        units=units,
        metadata=metadata,
    )


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

    abf = pyabf.ABF(str(filepath))

    return {
        "filepath": str(filepath),
        "abf_id": abf.abfID,
        "abf_version": abf.abfVersion,
        "n_channels": abf.channelCount,
        "n_sweeps": abf.sweepCount,
        "sampling_rate_hz": abf.dataRate,
        "duration_sec": abf.dataLengthSec,
        "n_samples_per_sweep": abf.sweepPointCount,
        "channel_units": abf.adcUnits,
        "channel_names": abf.adcNames,
        "protocol": getattr(abf, "protocol", ""),
    }
