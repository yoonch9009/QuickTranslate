from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quicktranslate.codex_client import (
    CodexAppServerClient,
    _classified_error,
    _TurnState,
    find_codex_executable,
)


class CodexClientTests(unittest.TestCase):
    def test_bundled_codex_is_preferred_over_an_older_path_executable(self) -> None:
        with TemporaryDirectory() as directory:
            bundled_root = Path(directory) / "OpenAI" / "Codex" / "bin"
            older = bundled_root / "old" / "codex.exe"
            newer = bundled_root / "new" / "codex.exe"
            older.parent.mkdir(parents=True)
            newer.parent.mkdir(parents=True)
            older.touch()
            newer.touch()
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            with (
                patch.dict("os.environ", {"LOCALAPPDATA": directory}),
                patch("quicktranslate.codex_client.shutil.which", return_value="C:/npm/codex.exe"),
            ):
                self.assertEqual(find_codex_executable(), str(newer))

    def test_path_codex_is_used_when_desktop_bundle_is_absent(self) -> None:
        with (
            patch.dict("os.environ", {"LOCALAPPDATA": "C:/desktop"}),
            patch("quicktranslate.codex_client.Path.is_dir", return_value=False),
            patch("quicktranslate.codex_client.shutil.which", return_value="C:/npm/codex.exe"),
        ):
            self.assertEqual(find_codex_executable(), "C:/npm/codex.exe")

    def test_only_final_answer_is_streamed_and_returned(self) -> None:
        emitted: list[str] = []
        client = CodexAppServerClient()
        turn = _TurnState(on_delta=emitted.append)
        client._turns["thread-1"] = turn

        client._handle_notification(
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread-1",
                    "item": {
                        "id": "commentary",
                        "type": "agentMessage",
                        "phase": "commentary",
                    },
                },
            }
        )
        client._handle_notification(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "commentary",
                    "delta": "번역을 시작합니다.",
                },
            }
        )
        client._handle_notification(
            {
                "method": "item/started",
                "params": {
                    "threadId": "thread-1",
                    "item": {
                        "id": "final",
                        "type": "agentMessage",
                        "phase": "final_answer",
                    },
                },
            }
        )
        client._handle_notification(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "itemId": "final",
                    "delta": "안녕하세요",
                },
            }
        )
        client._handle_notification(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "item": {
                        "id": "final",
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "안녕하세요",
                    },
                },
            }
        )
        client._handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"status": "completed", "error": None},
                },
            }
        )

        self.assertEqual(emitted, ["안녕하세요"])
        self.assertEqual(turn.final_text, "안녕하세요")
        self.assertTrue(turn.completed.is_set())

    def test_usage_limit_is_retryable_for_immediate_fallback(self) -> None:
        error = _classified_error("UsageLimitExceeded", "limit")

        self.assertTrue(error.retryable)
        self.assertIn("사용 한도", error.user_message)


if __name__ == "__main__":
    unittest.main()
