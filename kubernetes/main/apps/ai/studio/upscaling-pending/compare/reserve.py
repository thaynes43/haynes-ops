"""Reserve the one new output directory as the Studio storage owner."""

import json
import os
import re
import sys
from pathlib import Path

JOBS = Path("/jobs")


def main(arguments):
    code = "comparison.arguments_invalid"
    if len(arguments) == 1 and re.fullmatch(r"upscaling-op461-compare-[0-9a-f]{12}", arguments[0]):
        directory = JOBS / arguments[0]
        try:
            directory.mkdir(mode=0o770)
        except FileExistsError:
            code = "comparison.output_exists"
        except OSError:
            code = "comparison.output_unavailable"
        else:
            try:
                # Keep artifacts group-readable by Studio's storage identity.
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fchmod(fd, 0o2770)
                finally:
                    os.close(fd)
            except OSError:
                code = "comparison.output_unavailable"
            else:
                return 0
    print(json.dumps({"passed": False, "code": code}), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
