"""Точка входа установленной программы (собирается PyInstaller)."""

import sys

from leadscout.desktop import main

if __name__ == "__main__":
    sys.exit(main())
