from __future__ import annotations

import unittest

from quicktranslate.codex_client import (
    CodexAppServerClient,
    _classified_error,
    _TurnState,
)


class CodexClientTests(unittest.TestCase):
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
