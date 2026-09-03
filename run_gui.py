"""PyInstaller entry point for the standalone RayNeo Control GUI .exe.

Not part of the installable package -- just a thin launcher so PyInstaller
has a single top-level script to point at. Build with:

    pyinstaller RayNeoControl.spec

or, to regenerate that spec from scratch:

    pyinstaller --name RayNeoControl --onefile --windowed --clean run_gui.py
"""
from rayneo_control.gui import main

if __name__ == "__main__":
    main()
