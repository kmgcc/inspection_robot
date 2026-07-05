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
        audio._queue = queue.Queue()
        audio._thread = None
        audio._player_cache = None
        audio._tts_player_cache = None

    def tearDown(self) -> None:
        audio._queue = self.original_queue
        audio._thread = self.original_thread
        audio._player_cache = self.original_player_cache
        audio._tts_player_cache = self.original_tts_player_cache
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
        self.assertEqual(job.command, ["/usr/bin/paplay", str(self.root / "src/inspection_robot/static/audio/obstacle.wav")])

    def test_spoken_message_is_queued_without_fire_and_forget_popen(self) -> None:
        with (
            mock.patch.object(audio, "_ensure_worker"),
            mock.patch.object(audio.shutil, "which", side_effect=lambda name: "/usr/bin/espeak-ng" if name == "espeak-ng" else None),
            mock.patch.object(audio.subprocess, "Popen") as popen,
        ):
            payload, status = audio.start_spoken_message(self.root, "  检测到   A1 缺少 红瓶。  ")

        self.assertEqual(status, 200)
        self.assertTrue(payload["queued"])
        popen.assert_not_called()
        job = audio._queue.get_nowait()
        self.assertEqual(job.cue, "spoken")
        self.assertEqual(job.command, ["/usr/bin/espeak-ng", "-v", "zh", "检测到 A1 缺少 红瓶。"])

    def test_worker_runs_audio_jobs_in_fifo_order(self) -> None:
        commands: list[list[str]] = []
        kwargs_seen: list[dict[str, object]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            kwargs_seen.append(kwargs)
            return subprocess.CompletedProcess(command, 0)

        audio._queue.put(audio.AudioJob(cue="obstacle", command=["/usr/bin/paplay", "obstacle.wav"]))
        audio._queue.put(audio.AudioJob(cue="spoken", command=["/usr/bin/espeak-ng", "-v", "zh", "测试"]))
        audio._queue.put(None)

        with mock.patch.object(audio.subprocess, "run", side_effect=fake_run):
            audio._worker()

        self.assertEqual(commands, [["/usr/bin/paplay", "obstacle.wav"], ["/usr/bin/espeak-ng", "-v", "zh", "测试"]])
        self.assertTrue(all(kwargs["stdin"] is subprocess.DEVNULL for kwargs in kwargs_seen))
        self.assertTrue(all(kwargs["check"] is False for kwargs in kwargs_seen))

    def test_audio_debug_status_reports_cues_and_players(self) -> None:
        def fake_which(name: str) -> str | None:
            return f"/usr/bin/{name}" if name in {"paplay", "espeak-ng"} else None

        with mock.patch.object(audio.shutil, "which", side_effect=fake_which):
            payload = audio.audio_debug_status(self.root)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["players"]["paplay"], "/usr/bin/paplay")
        self.assertEqual(payload["tts_players"]["espeak-ng"], "/usr/bin/espeak-ng")
        self.assertTrue(payload["cues"]["first"]["exists"])
        self.assertTrue(payload["cues"]["following"]["exists"])


if __name__ == "__main__":
    unittest.main()
