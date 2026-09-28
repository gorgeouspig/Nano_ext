"""Raw binary data reader for nanopore current traces.

Reads current signals from raw binary files with configurable
data format (byte order, data type, header size, etc.).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from nano_ext.models import SignalData

# Mapping of human-readable names to numpy dtype strings
DTYPE_MAP = {
    "int16": np.int16,
    "int32": np.int32,
    "uint16": np.uint16,
    "float32": np.float32,
    "float64": np.float64,
    "int16_le": np.dtype("<i2"),
    "int16_be": np.dtype(">i2"),
    "int32_le": np.dtype("<i4"),
    "int32_be": np.dtype(">i4"),
    "float32_le": np.dtype("<f4"),
    "float32_be": np.dtype(">f4"),
    "float64_le": np.dtype("<f8"),
    "float64_be": np.dtype(">f8"),
}


def read_binary(
    filepath: str | Path,
    sampling_rate: float,
    dtype: str = "int16",
    header_bytes: int = 0,
    scale_factor: float = 1.0,
    offset: float = 0.0,
    max_samples: Optional[int] = None,
    units: str = "pA",
) -> SignalData:
    """Read a nanopore current trace from a raw binary file.

    The raw binary file is expected to contain a continuous sequence of
    samples in the specified data type. An optional header can be skipped.

    The signal is converted to float32 and scaled as:
        signal_pA = raw_value * scale_factor + offset

    Parameters
    ----------
    filepath : str or Path
        Path to the binary file.
    sampling_rate : float
        Sampling rate in Hz (must be provided; not stored in raw files).
    dtype : str
        Data type of samples. One of:
        "int16", "int32", "uint16", "float32", "float64",
        or with explicit endianness: "int16_le", "int16_be", etc.
    header_bytes : int
        Number of bytes to skip at the beginning of the file.
    scale_factor : float
        Multiplicative scale factor to convert raw values to current units.
    offset : float
        Additive offset after scaling.
    max_samples : int, optional
        Maximum number of samples to read. None reads all.
    units : str
        Current units after conversion (e.g., "pA", "nA").

    Returns
    -------
    SignalData
        Loaded signal data.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the dtype is unknown.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Binary file not found: {filepath}")

    if dtype not in DTYPE_MAP:
        raise ValueError(
            f"Unknown dtype: {dtype}. "
            f"Available: {', '.join(sorted(DTYPE_MAP.keys()))}"
        )

    np_dtype = DTYPE_MAP[dtype]

    # Memory-map the raw samples (no intermediate bytes copy of the file).
    n_raw = max(0, filepath.stat().st_size - header_bytes) // np.dtype(np_dtype).itemsize
    if n_raw == 0:
        raw_data = np.empty(0, dtype=np_dtype)
    else:
        raw_data = np.memmap(
            filepath, dtype=np_dtype, mode="r", offset=header_bytes, shape=(n_raw,)
        )
    if max_samples is not None:
        raw_data = raw_data[:max_samples]

    # Convert to float32 (exact for 16-bit ADC data) and apply scaling
    from nano_ext.io.abf_reader import _scale
    signal = _scale(raw_data, float(scale_factor), float(offset))

    metadata = {
        "format": "binary",
        "filepath": str(filepath),
        "original_dtype": dtype,
        "header_bytes": header_bytes,
        "scale_factor": scale_factor,
        "offset": offset,
        "n_raw_samples": len(raw_data),
    }

    return SignalData(
        signal=signal,
        sampling_rate=sampling_rate,
        units=units,
        metadata=metadata,
    )


def get_binary_info(
    filepath: str | Path,
    dtype: str = "int16",
    header_bytes: int = 0,
    sampling_rate: float = 100_000.0,
) -> dict:
    """Get summary information about a binary file.

    Parameters
    ----------
    filepath : str or Path
        Path to the binary file.
    dtype : str
        Expected data type of samples.
    header_bytes : int
        Number of header bytes to skip.
    sampling_rate : float
        Assumed sampling rate for duration calculation.

    Returns
    -------
    dict
        Dictionary with file information.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Binary file not found: {filepath}")

    np_dtype = DTYPE_MAP.get(dtype)
    if np_dtype is None:
        raise ValueError(f"Unknown dtype: {dtype}")

    file_size = filepath.stat().st_size
    data_bytes = file_size - header_bytes
    bytes_per_sample = np.dtype(np_dtype).itemsize
    n_samples = data_bytes // bytes_per_sample
    duration_sec = n_samples / sampling_rate

    return {
        "filepath": str(filepath),
        "file_size_bytes": file_size,
        "header_bytes": header_bytes,
        "data_bytes": data_bytes,
        "dtype": dtype,
        "bytes_per_sample": bytes_per_sample,
        "n_samples": n_samples,
        "sampling_rate_hz": sampling_rate,
        "estimated_duration_sec": duration_sec,
    }
