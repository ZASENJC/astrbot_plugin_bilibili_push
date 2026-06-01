from __future__ import annotations

import unittest

from tests.conftest import plugin


class _FakeService:
    async def fetch_recent_videos(self, _uid: int, _limit: int):
        return [
            plugin.FeedVideoItem(
                aid=2,
                bvid="BV1xx411c7mD",
                title="new",
                created_ts=2,
                author="UP",
            ),
            plugin.FeedVideoItem(
                aid=1,
                bvid="BV1xx411c7mE",
                title="old",
                created_ts=1,
                author="UP",
            ),
        ]


class BilibiliMonitorStateTest(unittest.IsolatedAsyncioTestCase):
    async def test_check_uid_videos_uses_persisted_baseline_after_restart(self) -> None:
        main = plugin.Main.__new__(plugin.Main)
        main.config = {"runtime_settings": {"latest_fetch_limit": 5}}
        main.service = _FakeService()
        main.session_initialized_uids = set()
        main._state = {"last_aid_123": 1, "last_bvid_123": "BV1xx411c7mE"}
        main._state_get = lambda key, default=None: main._state.get(key, default)

        def update_state(values):
            main._state.update(values)

        main._state_update = update_state

        new_items = await main._check_uid_videos(123)

        self.assertEqual([2], [item.aid for item in new_items])
        self.assertEqual(2, main._state["last_aid_123"])
        self.assertEqual("BV1xx411c7mD", main._state["last_bvid_123"])
        self.assertIn(123, main.session_initialized_uids)


if __name__ == "__main__":
    unittest.main()
