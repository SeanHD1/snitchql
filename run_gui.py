#!/usr/bin/env python3
"""SnitchQL GUI entry point.

Launches the PyQt6 DBISAM viewer. This is the script PyInstaller bundles into
the Windows .exe; running it directly (`python run_gui.py`) also works.
"""
from snitchql.gui import main

if __name__ == "__main__":
    main()
