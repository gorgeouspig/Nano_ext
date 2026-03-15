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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "determine_threshold",
    "detect_events",
    "binary_segmentation_bic",
    "analyze_sublevels",
]
