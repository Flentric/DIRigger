#!/usr/bin/env python3
"""Auto-rig a humanoid OBJ into a Dead Island player model.

With just a model (e.g. dragged onto autorig.bat) it asks what to make: loose
files, a Logan replacement inside the level .rpack files, or plain files.
With options it runs directly; see --help.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dirigger.rig.cli import main  # noqa: E402

if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 1 and args[0].lower().endswith(".obj") and sys.stdin.isatty():
        # dragged onto autorig.bat: ask what to make
        from dirigger.rig.wizard import run
        run(args[0])
    else:
        main()
