"""``python -m nano_ext.gui`` — start the GUI."""

from nano_ext.gui import run_gui

if __name__ == "__main__":  # guard required: analysis workers use "spawn"
    run_gui()
