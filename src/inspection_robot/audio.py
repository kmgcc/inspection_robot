from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_AUDIO_PATH = Path("src/inspection_robot/static/audio/youdowhatreversed.wav")
STATIC_AUDIO_DIR = Path("src/inspection_robot/static/audio")
CUED_AUDIO = {
    "obstacle": STATIC_AUDIO_DIR / "obstacle.wav",
    "first": STATIC_AUDIO_DIR / "first.wav",
    "following": STATIC_AUDIO_DIR / "following.wav",
    "default": DEFAULT_AUDIO_PATH,
}
PLAYER_CANDIDATES = ("paplay", "pw-play", "aplay", "ffplay")


def _playback_env() -> dict[str, str]:
    env = os.environ.copy()
    uid = os.getuid()
    runtime_dir = f"/run/user/{uid}"
    env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    env.setdefault("PULSE_SERVER", f"unix:{runtime_dir}/pulse/native")
    return env


def _build_command(player: str, audio_path: Path) -> list[str]:
    name = Path(player).name
    if name == "ffplay":
        return [player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio_path)]
    return [player, str(audio_path)]


def start_default_audio(project_root: Path) -> tuple[dict[str, Any], int]:
    return start_audio_cue(project_root, "default")


def start_audio_cue(project_root: Path, cue: str) -> tuple[dict[str, Any], int]:
    cue_key = cue.strip().lower() or "default"
    relative_path = CUED_AUDIO.get(cue_key)
    if relative_path is None:
        return {"ok": False, "error": f"unknown audio cue: {cue}"}, 400
    audio_path = project_root / relative_path
    if not audio_path.exists():
        return {"ok": False, "error": f"audio file not found: {audio_path}"}, 404

    player = next((path for name in PLAYER_CANDIDATES if (path := shutil.which(name))), None)
    if player is None:
        return {
            "ok": False,
            "error": "no audio player found; install or enable paplay, pw-play, aplay, or ffplay",
        }, 503

    try:
        subprocess.Popen(
            _build_command(player, audio_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_playback_env(),
            start_new_session=True,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)}, 500

    return {"ok": True, "cue": cue_key, "player": Path(player).name, "audio": str(audio_path)}, 200


def main() -> int:
    project_root = Path.cwd()
    payload, status = start_default_audio(project_root)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
