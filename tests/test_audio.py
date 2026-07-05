from __future__ import annotations

import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_robot import audio


class AudioPlaybackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        audio_dir = self.root / "src" / "inspection_robot" / "static" / "audio"
        audio_dir.mkdir(parents=True)
        (audio_dir / "obstacle.wav").write_bytes(b"RIFF")
        (audio_dir / "first.wav").write_bytes(b"RIFF")
        (audio_dir / "following.wav").write_bytes(b"RIFF")
        (audio_dir / "youdowhatreversed.wav").write_bytes(b"RIFF")

        self.original_queue = audio._queue
        self.original_thread = audio._thread
        self.original_player_cache = audio._player_cache
        self.original_tts_player_cache = audio._tts_player_cache
        self.original_last_job_result = audio._last_job_result
        audio._queue = queue.Queue()
        audio._thread = None
        audio._player_cache = None
        audio._tts_player_cache = None
        audio._last_job_result = None

    def tearDown(self) -> None:
        audio._queue = self.original_queue
        audio._thread = self.original_thread
        audio._player_cache = self.original_player_cache
        audio._tts_player_cache = self.original_tts_player_cache
        audio._last_job_result = self.original_last_job_result
        self.tmp.cleanup()

    def test_audio_cue_is_queued_as_worker_job(self) -> None:
        with (
            mock.patch.object(audio, "_ensure_worker"),
            mock.patch.object(audio.shutil, "which", side_effect=lambda name: "/usr/bin/paplay" if name == "paplay" else None),
        ):
            payload, status = audio.start_audio_cue(self.root, "obstacle")

        self.assertEqual(status, 200)
        self.assertTrue(payload["queued"])
        job = audio._queue.get_nowait()
        self.assertEqual(job.cue, "obstacle")
        self.assertEqual(job.commands, [["/usr/bin/paplay", str(self.root / "src/inspection_robot/static/audio/obstacle.wav")]])

    def test_spoken_message_is_queued_without_fire_and_forget_popen(self) -> None:
        with (
            mock.patch.object(audio, "_ensure_worker"),
            mock.patch.object(audio.shutil, "which", side_effect=lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None),
            mock.patch.object(audio.subprocess, "Popen") as popen,
        ):
            payload, status = audio.start_spoken_message(self.root, "  检测到   A1 缺少 红瓶。  ")

        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertIn("offline", payload["error"])
        popen.assert_not_called()

    def test_spoken_message_is_synthesized_by_ekho_then_queued_through_audio_player(self) -> None:
        synth_commands: list[list[str]] = []

        def fake_which(name: str) -> str | None:
            if name == "ekho":
                return "/usr/bin/ekho"
            if name == "pw-play":
                return "/usr/bin/pw-play"
            return None

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            synth_commands.append(command)
            Path(command[command.index("-o") + 1]).write_bytes(b"RIFF")
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.object(audio, "_ensure_worker"),
            mock.patch.object(audio.shutil, "which", side_effect=fake_which),
            mock.patch.object(audio.subprocess, "run", side_effect=fake_run),
        ):
            payload, status = audio.start_spoken_message(self.root, "  检测到   A1 缺少 红瓶。  ")

        self.assertEqual(status, 200)
        self.assertTrue(payload["queued"])
        self.assertEqual(payload["tts_player"], "ekho")
        self.assertEqual(synth_commands[0][:6], ["/usr/bin/ekho", "-v", "Mandarin", "-t", "wav", "-o"])
        job = audio._queue.get_nowait()
        self.assertEqual(job.cue, "spoken")
        self.assertEqual(job.commands[0][0], "/usr/bin/pw-play")
        self.assertEqual(job.cleanup_paths[0], Path(job.commands[0][1]))

    def test_piper_uses_stdin_and_configured_model(self) -> None:
        synth_commands: list[list[str]] = []
        kwargs_seen: list[dict[str, object]] = []

        def fake_which(name: str) -> str | None:
            if name == "piper":
                return "/usr/bin/piper"
            if name == "pw-play":
                return "/usr/bin/pw-play"
            return None

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            synth_commands.append(command)
            kwargs_seen.append(kwargs)
            Path(command[command.index("--output_file") + 1]).write_bytes(b"RIFF")
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.dict(audio.os.environ, {"ROBOT_PIPER_MODEL": "/opt/piper/zh.onnx"}, clear=False),
            mock.patch.object(audio, "_ensure_worker"),
            mock.patch.object(audio.shutil, "which", side_effect=fake_which),
            mock.patch.object(audio.subprocess, "run", side_effect=fake_run),
        ):
            payload, status = audio.start_spoken_message(self.root, "检测到 A1 缺少 红瓶。")

        self.assertEqual(status, 200)
        self.assertEqual(payload["tts_player"], "piper")
        self.assertEqual(synth_commands[0][:4], ["/usr/bin/piper", "--model", "/opt/piper/zh.onnx", "--output_file"])
        self.assertEqual(kwargs_seen[0]["input"], "检测到 A1 缺少 红瓶。")
        self.assertNotIn("stdin", kwargs_seen[0])

    def test_piper_is_discovered_from_default_directory_without_path_or_model_env(self) -> None:
        piper_dir = self.root / "piper"
        piper_dir.mkdir()
        piper_bin = piper_dir / "piper"
        piper_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        piper_bin.chmod(0o755)
        model = piper_dir / "zh_CN-huayan-x_low.onnx"
        model.write_bytes(b"model")
        synth_commands: list[list[str]] = []

        def fake_which(name: str) -> str | None:
            if name == "pw-play":
                return "/usr/bin/pw-play"
            return None

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            synth_commands.append(command)
            Path(command[command.index("--output_file") + 1]).write_bytes(b"RIFF")
            return subprocess.CompletedProcess(command, 0)

        with (
            mock.patch.dict(audio.os.environ, {"ROBOT_PIPER_DIR": str(piper_dir), "ROBOT_PIPER_MODEL": ""}, clear=False),
            mock.patch.object(audio, "_ensure_worker"),
            mock.patch.object(audio.shutil, "which", side_effect=fake_which),
            mock.patch.object(audio.subprocess, "run", side_effect=fake_run),
        ):
            payload, status = audio.start_spoken_message(self.root, "检测到 A区一号货架 缺少 手机。")

        self.assertEqual(status, 200)
        self.assertEqual(payload["tts_player"], "piper")
        self.assertEqual(synth_commands[0][:4], [str(piper_bin), "--model", str(model), "--output_file"])

    def test_edge_tts_is_not_selected_without_online_opt_in(self) -> None:
        def fake_which(name: str) -> str | None:
            if name == "edge-tts":
                return "/usr/bin/edge-tts"
            return None

        with (
            mock.patch.dict(audio.os.environ, {"ROBOT_ALLOW_ONLINE_TTS": "0"}, clear=False),
            mock.patch.object(audio.shutil, "which", side_effect=fake_which),
        ):
            payload, status = audio.start_spoken_message(self.root, "检测到 A1 缺少 红瓶。")

        self.assertEqual(status, 503)
        self.assertIn("offline", payload["error"])

    def test_worker_runs_audio_jobs_in_fifo_order(self) -> None:
        commands: list[list[str]] = []
        kwargs_seen: list[dict[str, object]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            kwargs_seen.append(kwargs)
            return subprocess.CompletedProcess(command, 0)

        audio._queue.put(audio.AudioJob(cue="obstacle", commands=[["/usr/bin/paplay", "obstacle.wav"]]))
        audio._queue.put(audio.AudioJob(cue="spoken", commands=[["/usr/bin/espeak-ng", "-v", "zh", "测试"]]))
        audio._queue.put(None)

        with (
            mock.patch.object(audio, "_prepare_audio_output"),
            mock.patch.object(audio.subprocess, "run", side_effect=fake_run),
        ):
            audio._worker()

        self.assertEqual(commands, [["/usr/bin/paplay", "obstacle.wav"], ["/usr/bin/espeak-ng", "-v", "zh", "测试"]])
        self.assertTrue(all(kwargs["stdin"] is subprocess.DEVNULL for kwargs in kwargs_seen))
        self.assertTrue(all(kwargs["check"] is False for kwargs in kwargs_seen))
        self.assertEqual(audio._last_job_result["cue"], "spoken")
        self.assertTrue(audio._last_job_result["ok"])

    def test_audio_debug_status_reports_cues_and_players(self) -> None:
        def fake_which(name: str) -> str | None:
            return f"/usr/bin/{name}" if name in {"paplay", "espeak-ng"} else None

        with mock.patch.object(audio.shutil, "which", side_effect=fake_which):
            payload = audio.audio_debug_status(self.root)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["players"]["paplay"], "/usr/bin/paplay")
        self.assertEqual(payload["tts_players"]["espeak-ng"], "/usr/bin/espeak-ng")
        self.assertFalse(payload["tts_policy"]["online_enabled"])
        self.assertTrue(payload["cues"]["first"]["exists"])
        self.assertTrue(payload["cues"]["following"]["exists"])


if __name__ == "__main__":
    unittest.main()
