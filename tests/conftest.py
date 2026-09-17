"""Put `tests/` on the path so the fixture module imports by name."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
