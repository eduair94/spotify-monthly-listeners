import sys
from pathlib import Path

# The tests import the tracker package from the repository root and helpers.py from here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
