"""Pytest configuration for the healthquery-client test suite."""

import sys
from pathlib import Path


# Add the directory *above* the package so that ``import healthquery_client``
# resolves the package (not the bare module files inside it).
PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))