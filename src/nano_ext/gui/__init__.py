"""Browser-based GUI for Nano_ext (``nano-ext gui``).

Requires the optional dependencies: ``pip install "nano-ext[gui]"``.
"""

from __future__ import annotations


def run_gui(*args, **kwargs):
    """Start the GUI server; see :func:`nano_ext.gui.app.run_gui`."""
    _require_dash()
    from nano_ext.gui.app import run_gui as _run
    return _run(*args, **kwargs)


def create_app(*args, **kwargs):
    """Create the Dash app; see :func:`nano_ext.gui.app.create_app`."""
    _require_dash()
    from nano_ext.gui.app import create_app as _create
    return _create(*args, **kwargs)


def _require_dash() -> None:
    try:
        import dash  # noqa: F401
        import plotly  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "The GUI needs Dash and Plotly.\n"
            'Install them with:  pip install "nano-ext[gui]"'
        ) from exc


__all__ = ["run_gui", "create_app"]
