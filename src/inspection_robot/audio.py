from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
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
PLAYER_CANDIDATES = ("pw-play", "paplay", "aplay", "ffplay", "mpv")
MP3_PLAYER_CANDIDATES = ("ffplay", "mpv")
TTS_PLAYER_CANDIDATES = ("ekho", "piper", "edge-tts", "espeak-ng", "espeak", "spd-say")
OFFLINE_CHINESE_TTS_PLAYERS = {"ekho", "piper"}
ONLINE_TTS_PLAYERS = {"edge-tts"}
UNRELIABLE_CHINESE_TTS_PLAYERS = {"espeak-ng", "espeak", "spd-say"}
DEFAULT_PIPER_DIRS = (Path("/home/pi/temp/piper"), Path("/opt/piper"))
DEFAULT_PIPER_MODELS = (
    "zh_CN-huayan-x_low.onnx",
    "zh_CN-huayan-medium.onnx",
    "model.onnx",
)

# 单条 cue 播放超时，避免某次播放卡死阻塞后续所有 cue。
_PLAYBACK_TIMEOUT_SECONDS = 15.0

# 单线程播放队列：避免并发子进程与 PulseAudio 冲突，保证 cue/TTS 顺序播放。
# 之前用 Popen fire-and-forget，引用丢失 + 多进程抢 PulseAudio，导致只有第一遍出声。
@dataclass(frozen=True)
class AudioJob:
    cue: str
    commands: list[list[str]]
    cleanup_paths: tuple[Path, ...] = ()
    timeout_seconds: float = _PLAYBACK_TIMEOUT_SECONDS


_queue: "queue.Queue[AudioJob | None]" = queue.Queue()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_player_cache: str | None = None
_tts_player_cache: str | None = None
_last_job_result: dict[str, Any] | None = None
_last_job_lock = threading.Lock()


def _find_player() -> str | None:
    global _player_cache
    if _player_cache is not None:
        return _player_cache
    players = _available_audio_players()
    if players:
        _player_cache = players[0]
        return players[0]
    return None


def _available_audio_players() -> list[str]:
    return [path for name in PLAYER_CANDIDATES if (path := shutil.which(name))]


def _available_audio_players_for_suffix(suffix: str) -> list[str]:
    if suffix.lower() == ".mp3":
        return [path for name in MP3_PLAYER_CANDIDATES if (path := shutil.which(name))]
    return _available_audio_players()


def _available_tts_players() -> list[str]:
    players: list[str] = []
    for name in TTS_PLAYER_CANDIDATES:
        if name in ONLINE_TTS_PLAYERS and not _allow_online_tts():
            continue
        path = _find_tts_executable(name)
        if path:
            players.append(path)
    return players


def _find_tts_executable(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path
    if name != "piper":
        return None
    for directory in _candidate_piper_dirs():
        candidate = directory / "piper"
        if candidate.exists():
            return str(candidate)
    return None


def _candidate_piper_dirs() -> list[Path]:
    raw_dirs = [os.environ.get("ROBOT_PIPER_DIR", "").strip()]
    raw_dirs.extend(str(path) for path in DEFAULT_PIPER_DIRS)
    dirs: list[Path] = []
    for raw in raw_dirs:
        if not raw:
            continue
        path = Path(raw)
        if path not in dirs:
            dirs.append(path)
    return dirs


def _find_tts_player() -> str | None:
    global _tts_player_cache
    if _tts_player_cache is not None:
        return _tts_player_cache
    players = _available_tts_players()
    if players:
        _tts_player_cache = players[0]
        return players[0]
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
    if name == "mpv":
        return [player, "--no-video", "--really-quiet", str(audio_path)]
    return [player, str(audio_path)]


def _build_audio_commands(players: list[str], audio_path: Path) -> list[list[str]]:
    return [_build_command(player, audio_path) for player in players]


def _worker() -> None:
    while True:
        item = _queue.get()
        if item is None:
            return
        _prepare_audio_output()
        attempts: list[dict[str, Any]] = []
        ok = False
        try:
            for command in item.commands:
                attempt = _run_playback_command(command, item.timeout_seconds)
                attempts.append(attempt)
                if attempt.get("returncode") == 0:
                    ok = True
                    break
        finally:
            for path in item.cleanup_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            _record_last_job_result(item.cue, ok, attempts)


def _run_playback_command(command: list[str], timeout_seconds: float) -> dict[str, Any]:
    attempt: dict[str, Any] = {"player": Path(command[0]).name, "command": command}
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=_playback_env(),
            timeout=timeout_seconds,
            text=True,
            check=False,
        )
    except OSError as exc:
        attempt["error"] = str(exc)
        return attempt
    except subprocess.TimeoutExpired:
        attempt["error"] = "timeout"
        return attempt
    attempt["returncode"] = completed.returncode
    output = " ".join((completed.stdout or "").split())
    if output:
        attempt["output"] = output[:300]
    return attempt


def _record_last_job_result(cue: str, ok: bool, attempts: list[dict[str, Any]]) -> None:
    global _last_job_result
    with _last_job_lock:
        _last_job_result = {"cue": cue, "ok": ok, "attempts": attempts}


def _prepare_audio_output() -> None:
    if os.environ.get("ROBOT_AUDIO_AUTOFIX", "1").strip().lower() in {"0", "false", "no"}:
        return
    wpctl = shutil.which("wpctl")
    if wpctl is None:
        return
    volume = os.environ.get("ROBOT_AUDIO_VOLUME", "0.80").strip()
    try:
        volume_value = float(volume)
    except ValueError:
        volume_value = 0.80
    volume_value = min(max(volume_value, 0.0), 1.5)
    env = _playback_env()
    for command in (
        [wpctl, "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
        [wpctl, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{volume_value:.2f}"],
    ):
        try:
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                env=env,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
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

    players = _available_audio_players()
    if not players:
        return {
            "ok": False,
            "error": "no audio player found; install or enable paplay, pw-play, aplay, or ffplay",
        }, 503

    _ensure_worker()
    _queue.put(AudioJob(cue=cue_key, commands=_build_audio_commands(players, audio_path)))

    return {
        "ok": True,
        "cue": cue_key,
        "player": Path(players[0]).name,
        "players": [Path(player).name for player in players],
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
    tts_players = _available_tts_players()
    if not tts_players:
        return {
            "ok": False,
            "error": "no offline Chinese TTS command found; copy and install ekho, or copy piper plus a Chinese model",
        }, 503

    attempts: list[dict[str, Any]] = []
    for player in tts_players:
        payload, status = _try_queue_spoken_message(player, text)
        if status == 200:
            return payload, status
        attempts.append(payload)

    error = _speech_failure_message(text, attempts)
    return {"ok": False, "error": error, "attempts": attempts}, 503


def _speech_text(message: str) -> str:
    return " ".join(str(message).split())[:160]


def _try_queue_spoken_message(player: str, message: str) -> tuple[dict[str, Any], int]:
    name = Path(player).name
    if _contains_cjk(message) and name in UNRELIABLE_CHINESE_TTS_PLAYERS and not _allow_unreliable_chinese_tts():
        return {
            "ok": False,
            "tts_player": name,
            "error": f"{name} is skipped for Chinese text because it does not produce intelligible Mandarin",
        }, 503

    if name == "spd-say":
        _ensure_worker()
        _queue.put(AudioJob(cue="spoken", commands=[_build_tts_command(player, message)]))
        return {"ok": True, "cue": "spoken", "player": name, "message": message, "queued": True}, 200

    suffix = ".mp3" if name == "edge-tts" else ".wav"
    players = _available_audio_players_for_suffix(suffix)
    if not players:
        return {
            "ok": False,
            "tts_player": name,
            "error": f"no audio player found for {suffix} speech; install or enable ffplay/mpv/paplay/pw-play/aplay",
        }, 503

    speech_path, synth_error = _synthesize_speech_to_file(player, message)
    if speech_path is None:
        return {"ok": False, "tts_player": name, "error": synth_error or "failed to synthesize speech"}, 503

    _ensure_worker()
    _queue.put(
        AudioJob(
            cue="spoken",
            commands=_build_audio_commands(players, speech_path),
            cleanup_paths=(speech_path,),
        )
    )
    return {
        "ok": True,
        "cue": "spoken",
        "player": Path(players[0]).name,
        "players": [Path(item).name for item in players],
        "tts_player": name,
        "speech_format": speech_path.suffix.lstrip("."),
        "message": message,
        "queued": True,
    }, 200


def _contains_cjk(message: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in message)


def _allow_unreliable_chinese_tts() -> bool:
    return os.environ.get("ROBOT_ALLOW_UNRELIABLE_CHINESE_TTS", "").strip().lower() in {"1", "true", "yes"}


def _allow_online_tts() -> bool:
    return os.environ.get("ROBOT_ALLOW_ONLINE_TTS", "").strip().lower() in {"1", "true", "yes"}


def _speech_failure_message(message: str, attempts: list[dict[str, Any]]) -> str:
    if _contains_cjk(message):
        return (
            "Chinese speech needs offline ekho or a configured piper Chinese model. "
            "edge-tts is disabled by default because the car has no network; "
            "espeak/spd-say were not used because they usually read Chinese as unintelligible phonemes."
        )
    if attempts:
        return str(attempts[-1].get("error") or "failed to synthesize speech")
    return "no usable TTS command found"


def _build_tts_command(player: str, message: str) -> list[str]:
    name = Path(player).name
    if name == "spd-say":
        return [player, message]
    return [player, "-v", "zh", message]


def _synthesize_speech_to_file(player: str, message: str) -> tuple[Path | None, str | None]:
    name = Path(player).name
    suffix = ".mp3" if name == "edge-tts" else ".wav"
    handle = tempfile.NamedTemporaryFile(prefix="inspection_robot_tts_", suffix=suffix, delete=False)
    speech_path = Path(handle.name)
    handle.close()
    command: list[str]
    run_kwargs: dict[str, Any] = {}
    if name == "ekho":
        voice = os.environ.get("ROBOT_EKHO_VOICE", "Mandarin").strip() or "Mandarin"
        command = [player, "-v", voice, "-t", "wav", "-o", str(speech_path), message]
    elif name == "piper":
        model = _piper_model_path()
        if not model:
            speech_path.unlink(missing_ok=True)
            return None, "ROBOT_PIPER_MODEL is not set and no default Chinese Piper model was found"
        command = [player, "--model", model, "--output_file", str(speech_path)]
        run_kwargs["input"] = message
    elif name == "edge-tts":
        voice = os.environ.get("ROBOT_EDGE_TTS_VOICE", "zh-CN-XiaoxiaoNeural").strip() or "zh-CN-XiaoxiaoNeural"
        command = [player, "--voice", voice, "--text", message, "--write-media", str(speech_path)]
    elif name in {"espeak-ng", "espeak"}:
        voice = os.environ.get("ROBOT_ESPEAK_VOICE", "zh").strip() or "zh"
        command = [player, "-v", voice, "-w", str(speech_path), message]
    else:
        speech_path.unlink(missing_ok=True)
        return None, f"unsupported TTS command: {name}"
    try:
        subprocess_kwargs: dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "timeout": 8.0,
            "check": False,
        }
        if "input" in run_kwargs:
            subprocess_kwargs.update(run_kwargs)
        else:
            subprocess_kwargs["stdin"] = subprocess.DEVNULL
        completed = subprocess.run(
            command,
            **subprocess_kwargs,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        speech_path.unlink(missing_ok=True)
        return None, str(exc)
    if completed.returncode != 0:
        speech_path.unlink(missing_ok=True)
        output = " ".join((completed.stdout or "").split())
        return None, output[:300] or f"{Path(player).name} exited with {completed.returncode}"
    return speech_path, None


def _piper_model_path() -> str | None:
    configured = os.environ.get("ROBOT_PIPER_MODEL", "").strip()
    if configured:
        return configured
    for directory in _candidate_piper_dirs():
        for filename in DEFAULT_PIPER_MODELS:
            candidate = directory / filename
            if candidate.exists():
                return str(candidate)
    return None


def audio_debug_status(project_root: Path) -> dict[str, Any]:
    players = {name: shutil.which(name) for name in PLAYER_CANDIDATES}
    tts_players = {name: _find_tts_executable(name) for name in TTS_PLAYER_CANDIDATES}
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
        "tts_policy": {
            "offline_chinese": sorted(OFFLINE_CHINESE_TTS_PLAYERS),
            "online_enabled": _allow_online_tts(),
            "unreliable_chinese_enabled": _allow_unreliable_chinese_tts(),
            "piper_model": _piper_model_path(),
        },
        "cues": cues,
        "default_sink": _command_output(["pactl", "get-default-sink"]) or _command_output(["wpctl", "status"], limit=600),
        "worker_alive": _thread is not None and _thread.is_alive(),
        "queue_size": _queue.qsize(),
        "last_job": _last_job_result,
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
