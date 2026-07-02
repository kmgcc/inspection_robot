from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot.vision.tag_detector import VisionDependencyError, iter_detections


def main() -> int:
    device = int(os.environ.get("CAMERA_DEVICE", "0"))
    limit = int(os.environ.get("TAG_TEST_LIMIT", "0"))
    print(f"side camera tag test: device={device}, limit={limit or 'infinite'}", flush=True)
    try:
        for index, detection in enumerate(iter_detections(device=device, cooldown_seconds=0.8), start=1):
            print(json.dumps({"index": index, "detection": detection}, ensure_ascii=False), flush=True)
            if limit and index >= limit:
                break
    except KeyboardInterrupt:
        print("stopped by user", flush=True)
        return 0
    except VisionDependencyError as exc:
        print(f"vision dependency error: {exc}", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
