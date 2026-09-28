"""Event detection modules (threshold, event detection, changepoint, sublevel)."""

# Lazy imports to allow incremental development.
# Each sub-module is imported when first accessed.


def __getattr__(name: str):
    if name == "determine_threshold":
        from nano_ext.detection.threshold import determine_threshold

        return determine_threshold
    elif name == "detect_events":
        from nano_ext.detection.events import detect_events

        return detect_events
    elif name == "binary_segmentation_bic":
        from nano_ext.detection.changepoint import binary_segmentation_bic

        return binary_segmentation_bic
    elif name == "analyze_sublevels":
        from nano_ext.detection.sublevel import analyze_sublevels

        return analyze_sublevels
    elif name == "bocpd":
        from nano_ext.detection.bocpd import bocpd

        return bocpd
    elif name == "fit_dpgmm_1d":
        from nano_ext.detection.bayes_mixture import fit_dpgmm_1d

        return fit_dpgmm_1d
    elif name == "fit_sticky_hdp_hmm":
        from nano_ext.detection.hdphmm import fit_sticky_hdp_hmm

        return fit_sticky_hdp_hmm
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "determine_threshold",
    "detect_events",
    "binary_segmentation_bic",
    "analyze_sublevels",
    "bocpd",
    "fit_dpgmm_1d",
    "fit_sticky_hdp_hmm",
]
