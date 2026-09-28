"""Signal and event-population analysis utilities.

PSD / spectral diagnostics, Dirichlet-process event clustering, and Bayesian
event statistics (capture rate, dwell-time mixtures).
"""

from nano_ext.analysis.spectrum import (
    PSDResult,
    compute_psd,
    estimate_filter_cutoff,
    compare_psd,
    make_psd_figure,
)
from nano_ext.analysis.clustering import (
    EventClusterResult,
    available_features,
    cluster_events,
    event_features,
)
from nano_ext.analysis.bayes_stats import (
    DwellTimePosterior,
    RatePosterior,
    capture_rate_posterior,
    dwell_time_mixture,
    summarize_event_statistics,
)

__all__ = [
    "PSDResult",
    "compute_psd",
    "estimate_filter_cutoff",
    "compare_psd",
    "make_psd_figure",
    "EventClusterResult",
    "available_features",
    "cluster_events",
    "event_features",
    "DwellTimePosterior",
    "RatePosterior",
    "capture_rate_posterior",
    "dwell_time_mixture",
    "summarize_event_statistics",
]
