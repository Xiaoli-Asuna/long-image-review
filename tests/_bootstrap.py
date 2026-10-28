"""Keep plain unittest discovery compatible with source and embedded Python."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
