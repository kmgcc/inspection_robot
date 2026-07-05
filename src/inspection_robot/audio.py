from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
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
TTS_PLAYER_CANDIDATES = ("espeak-ng", "espeak", "spd-say")

# 单条 cue 播放超时，避免某次播放卡死阻塞后续所有 cue。
_PLAYBACK_TIMEOUT_SECONDS = 15.0

# 单线程播放队列：避免并发子进程与 PulseAudio 冲突，保证 cue/TTS 顺序播放。
# 之前用 Popen fire-and-forget，引用丢失 + 多进程抢 PulseAudio，导致只有第一遍出声。
@dataclass(frozen=True)
class AudioJob:
    cue: str
    command: list[str]
    timeout_seconds: float = _PLAYBACK_TIMEOUT_SECONDS


_queue: "queue.Queue[AudioJob | None]" = queue.Queue()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_player_cache: str | None = None
_tts_player_cache: str | None = None


def _find_player() -> str | None:
    global _player_cache
    if _player_cache is not None:
        return _player_cache
    for name in PLAYER_CANDIDATES:
        path = shutil.which(name)
        if path:
            _player_cache = path
            return path
    return None


def _find_tts_player() -> str | None:
    global _tts_player_cache
    if _tts_player_cache is not None:
        return _tts_player_cache
    for name in TTS_PLAYER_CANDIDATES:
        path = shutil.which(name)
        if path:
            _tts_player_cache = path
            return path
    return None


def _playback_env() -> dict[str, str]:
    env = os.environ.copy()
    uid_getter = getattr(os, "getuid", None)
    uid = uid_getter() if callable(uid_getter) else 0
    runtime_dir = f"/run/user/{uid}"
    env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
    env.setdefault("PULSE_SERVER", f"unix:{runtime_dir}/pulse/native")
    return env


def _build_command(player: str, audio_path: Path) -> list[str]:
    name = Path(player).name
    if name == "ffplay":
        return [player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio_path)]
    return [player, str(audio_path)]


def _worker() -> None:
    while True:
        item = _queue.get()
        if item is None:
            return
        try:
            subprocess.run(
                item.command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=_playback_env(),
                timeout=item.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            # 播放失败不阻塞主流程；下一次 cue 仍可正常入队。
            pass


def _ensure_worker() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_worker, daemon=True, name="audio-player")
        _thread.start()


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

    player = _find_player()
    if player is None:
        return {
            "ok": False,
            "error": "no audio player found; install or enable paplay, pw-play, aplay, or ffplay",
        }, 503

    _ensure_worker()
    _queue.put(AudioJob(cue=cue_key, command=_build_command(player, audio_path)))

    return {
        "ok": True,
        "cue": cue_key,
        "player": Path(player).name,
        "audio": str(audio_path),
        "queued": True,
    }, 200


def shutdown() -> None:
    """停止后台播放线程。进程退出时 daemon 线程会自动结束，通常无需手动调用。"""
    if _thread is not None and _thread.is_alive():
        _queue.put(None)
        _thread.join(timeout=2.0)


def start_spoken_message(project_root: Path, message: str) -> tuple[dict[str, Any], int]:
    del project_root
    text = _speech_text(message)
    if not text:
        return {"ok": False, "error": "empty speech message"}, 400
    player = _find_tts_player()
    if player is None:
        return {"ok": False, "error": "no local TTS command found; install espeak-ng, espeak, or spd-say"}, 503

    _ensure_worker()
    _queue.put(AudioJob(cue="spoken", command=_build_tts_command(player, text)))

    return {"ok": True, "cue": "spoken", "player": Path(player).name, "message": text, "queued": True}, 200


def _speech_text(message: str) -> str:
    return " ".join(str(message).split())[:160]


def _build_tts_command(player: str, message: str) -> list[str]:
    name = Path(player).name
    if name == "spd-say":
        return [player, message]
    return [player, "-v", "zh", message]


def audio_debug_status(project_root: Path) -> dict[str, Any]:
    players = {name: shutil.which(name) for name in PLAYER_CANDIDATES}
    tts_players = {name: shutil.which(name) for name in TTS_PLAYER_CANDIDATES}
    cues = {
        cue: {
            "path": str(project_root / relative_path),
            "exists": (project_root / relative_path).exists(),
        }
        for cue, relative_path in CUED_AUDIO.items()
    }
    playback_env = _playback_env()
    return {
        "ok": True,
        "players": players,
        "tts_players": tts_players,
        "cues": cues,
        "default_sink": _command_output(["pactl", "get-default-sink"]) or _command_output(["wpctl", "status"], limit=600),
        "playback_env": {
            "XDG_RUNTIME_DIR": playback_env.get("XDG_RUNTIME_DIR"),
            "PULSE_SERVER": playback_env.get("PULSE_SERVER"),
        },
    }


def _command_output(command: list[str], *, limit: int = 200) -> str | None:
    if shutil.which(command[0]) is None:
        return None
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = " ".join(completed.stdout.split())
    return output[:limit] if output else None


def main() -> int:
    project_root = Path.cwd()
    payload, status = start_default_audio(project_root)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
