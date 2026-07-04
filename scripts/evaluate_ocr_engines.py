from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare available OCR engines on the same image set.")
    parser.add_argument("images", nargs="+", help="Image files captured from the side camera.")
    parser.add_argument("--psm", default="7", help="Tesseract page segmentation mode.")
    args = parser.parse_args()

    runners = _available_runners(args.psm)
    if not runners:
        print(json.dumps({"ok": False, "error": "no OCR engines available"}, ensure_ascii=False, indent=2))
        return 1

    rows: list[dict[str, Any]] = []
    for image in [Path(item) for item in args.images]:
        for name, runner in runners.items():
            started = time.perf_counter()
            try:
                text = runner(image)
                error = None
            except Exception as exc:
                text = None
                error = str(exc)
            rows.append(
                {
                    "image": str(image),
                    "engine": name,
                    "text": text,
                    "error": error,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
                }
            )
    print(json.dumps({"ok": True, "engines": list(runners), "results": rows}, ensure_ascii=False, indent=2))
    return 0


def _available_runners(psm: str) -> dict[str, Callable[[Path], str | None]]:
    runners: dict[str, Callable[[Path], str | None]] = {}
    try:
        import pytesseract
    except ImportError:
        pytesseract = None
    if pytesseract is not None:
        runners["pytesseract"] = lambda path: _clean(pytesseract.image_to_string(str(path), config=f"--psm {psm}"))

    try:
        from paddleocr import PaddleOCR
    except ImportError:
        PaddleOCR = None
    if PaddleOCR is not None:
        ocr = PaddleOCR(use_angle_cls=True, lang="en")
        runners["paddleocr"] = lambda path: _paddle_text(ocr.ocr(str(path), cls=True))
    return runners


def _paddle_text(result: object) -> str | None:
    texts: list[str] = []
    if isinstance(result, list):
        for page in result:
            if not isinstance(page, list):
                continue
            for line in page:
                if (
                    isinstance(line, list)
                    and len(line) >= 2
                    and isinstance(line[1], (list, tuple))
                    and line[1]
                ):
                    texts.append(str(line[1][0]))
    return _clean(" ".join(texts))


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    return cleaned or None


if __name__ == "__main__":
    raise SystemExit(main())
