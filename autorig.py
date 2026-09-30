#!/usr/bin/env python3
"""Auto-rig a humanoid OBJ into a Dead Island player model. Run with --help."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dirigger.rig.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
