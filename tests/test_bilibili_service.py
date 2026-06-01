from __future__ import annotations

import unittest

from tests.conftest import plugin


class _CredentialManager:
    async def get_credential(self):
        return None


class _FailingVideoContext:
    def __init__(self) -> None:
        self.sent_chains: list[object] = []

    async def send_message(self, _target, chain):
        self.sent_chains.append(chain)
        if chain.chain and getattr(chain.chain[0], "type", "") == "Video":
            raise RuntimeError("video upload timed out")
        return True


class _FakeLinkResolver:
    async def extract_parse_target(self, _messages, _text):
        return plugin.ParseTarget(bvid="BV1xx411c7mD", raw_input="BV1xx411c7mD")


class _FakeCardService:
    def __init__(self, card) -> None:
        self.card = card

    async def fetch_video_card(self, _target):
        return self.card


class _FakeDebouncer:
    def update_ttl(self, _ttl_seconds) -> None:
        return None

    def hit_link(self, _session, _key) -> bool:
        return False

    def hit_resource(self, _session, _key) -> bool:
        return False


class _FakeEvent:
    message_str = "BV1xx411c7mD"
    unified_msg_origin = "aiocqhttp:GroupMessage:123"

    def get_sender_id(self):
        return "10001"

    def get_self_id(self):
        return "10000"

    def get_messages(self):
        return []

    def get_platform_id(self):
        return "aiocqhttp"

    def chain_result(self, chain):
        return chain


class _DirectVideo:
    async def get_download_url(self, page_index: int = 0):
        return {
            "format": "mp4",
            "durl": [
                {
                    "url": f"https://upos.example/video-{page_index}.mp4",
                }
            ],
        }


class _RecordingDownloadClient:
    def __init__(self) -> None:
        self.downloaded_urls: list[str] = []


class _DirectStream:
    url = "https://upos.example/video-0.mp4"


class _DirectStreamDetecter:
    def __init__(self, _download_url_data) -> None:
        pass

    def detect_best_streams(self, **_kwargs):
        return [_DirectStream()]


class _FakeUser:
    def __init__(self, uid: int, credential=None) -> None:
        self.uid = uid
        self.credential = credential

    async def get_videos(self, **_kwargs):
        raise RuntimeError("未匹配到用户动态页渲染数据")

    async def get_media_list(self, **_kwargs):
        return {
            "media_list": [
                {
                    "id": 99,
                    "bv_id": "not-a-bvid",
                    "title": "malformed title",
                    "pubtime": 1,
                },
                {
                    "id": 12,
                    "bv_id": "BV1xx411c7mD",
                    "title": "fallback title",
                    "pubtime": 123456,
                    "upper": {"name": "fallback up"},
                    "cover": {"unexpected": "shape"},
                    "intro": "fallback desc",
                }
            ]
        }


class BilibiliServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_recent_videos_falls_back_to_media_list(self) -> None:
        original_user = plugin.User
        plugin.User = _FakeUser
        try:
            service = plugin.BilibiliService(
                client=None,
                credential_manager=_CredentialManager(),
                content_config_getter=lambda: {},
                runtime_config_getter=lambda: {},
                cache_dir=self.tmp_path,
            )

            items = await service.fetch_recent_videos(uid=42, limit=5)
        finally:
            plugin.User = original_user

        self.assertEqual(1, len(items))
        item = items[0]
        self.assertEqual(12, item.aid)
        self.assertEqual("BV1xx411c7mD", item.bvid)
        self.assertEqual("fallback title", item.title)
        self.assertEqual(123456, item.created_ts)
        self.assertEqual("fallback up", item.author)
        self.assertEqual("", item.cover_url)
        self.assertEqual("fallback desc", item.desc)

    async def test_prepare_video_file_accepts_direct_mp4_stream(self) -> None:
        client = _RecordingDownloadClient()
        service = plugin.BilibiliService(
            client=client,
            credential_manager=_CredentialManager(),
            content_config_getter=lambda: {
                "send_direct_video": True,
                "video_max_size_mb": 90,
            },
            runtime_config_getter=lambda: {},
            cache_dir=self.tmp_path,
        )

        async def fake_download_stream(url, output_path, _headers, _timeout_seconds, _max_bytes):
            client.downloaded_urls.append(url)
            output_path.write_bytes(b"fake mp4")
            return output_path

        service._download_stream = fake_download_stream

        original_detecter = plugin.VideoDownloadURLDataDetecter
        plugin.VideoDownloadURLDataDetecter = _DirectStreamDetecter
        try:
            output_path = await service._prepare_video_file(_DirectVideo(), "BV1xx411c7mD", 0)
        finally:
            plugin.VideoDownloadURLDataDetecter = original_detecter

        self.assertEqual(self.tmp_path / "BV1xx411c7mD-p1-direct.mp4", output_path)
        self.assertEqual(["https://upos.example/video-0.mp4"], client.downloaded_urls)
        self.assertEqual(b"fake mp4", output_path.read_bytes())

    async def test_send_card_to_targets_falls_back_to_rich_text_when_video_send_fails(self) -> None:
        main = plugin.Main.__new__(plugin.Main)
        context = _FailingVideoContext()
        main.context = context
        main.config = {"content_settings": {"send_direct_video": True, "send_rich_text": False}}
        card = plugin.VideoCard(
            aid=12,
            bvid="BV1xx411c7mD",
            title="fallback title",
            link="https://www.bilibili.com/video/BV1xx411c7mD",
            up_name="fallback up",
            cover_url="",
            desc="fallback desc",
            duration_seconds=60,
            pub_ts=123456,
            view=1,
            like=2,
            danmaku=3,
            reply=4,
            favorite=5,
            coin=6,
            share=7,
            video_path=self.tmp_path / "video.mp4",
        )
        card.video_path.write_bytes(b"fake mp4")

        result = await main._send_card_to_targets(card, ["aiocqhttp:GroupMessage:123"])

        self.assertEqual({"target_success": 1, "target_failure": 0}, result)
        self.assertEqual(2, len(context.sent_chains))
        self.assertEqual("Video", getattr(context.sent_chains[0].chain[0], "type", ""))
        self.assertIsInstance(context.sent_chains[1].chain[0], str)

    async def test_passive_parse_sends_video_with_rich_text_fallback_itself(self) -> None:
        main = plugin.Main.__new__(plugin.Main)
        context = _FailingVideoContext()
        main.context = context
        main.config = {"content_settings": {"send_direct_video": True, "send_rich_text": False}}
        main.debouncer = _FakeDebouncer()
        main.link_resolver = _FakeLinkResolver()
        main._is_passive_session_allowed = lambda _event: True
        main._cleanup_media_cache = self._noop_async
        main._attach_qq_emoji_reaction = self._noop_async
        card = plugin.VideoCard(
            aid=12,
            bvid="BV1xx411c7mD",
            title="fallback title",
            link="https://www.bilibili.com/video/BV1xx411c7mD",
            up_name="fallback up",
            cover_url="",
            desc="fallback desc",
            duration_seconds=60,
            pub_ts=123456,
            view=1,
            like=2,
            danmaku=3,
            reply=4,
            favorite=5,
            coin=6,
            share=7,
            video_path=self.tmp_path / "video.mp4",
        )
        card.video_path.write_bytes(b"fake mp4")
        main.service = _FakeCardService(card)

        yielded = [item async for item in main.on_message(_FakeEvent())]

        self.assertEqual([], yielded)
        self.assertEqual(2, len(context.sent_chains))
        self.assertEqual("Video", getattr(context.sent_chains[0].chain[0], "type", ""))
        self.assertIsInstance(context.sent_chains[1].chain[0], str)

    async def test_video_send_cooldown_skips_retrying_failed_destination(self) -> None:
        main = plugin.Main.__new__(plugin.Main)
        context = _FailingVideoContext()
        main.context = context
        main.config = {"content_settings": {"send_direct_video": True, "send_rich_text": False}}
        main._video_send_failure_until = {"aiocqhttp:GroupMessage:123": plugin.time.time() + 60}
        card = plugin.VideoCard(
            aid=12,
            bvid="BV1xx411c7mD",
            title="fallback title",
            link="https://www.bilibili.com/video/BV1xx411c7mD",
            up_name="fallback up",
            cover_url="",
            desc="fallback desc",
            duration_seconds=60,
            pub_ts=123456,
            view=1,
            like=2,
            danmaku=3,
            reply=4,
            favorite=5,
            coin=6,
            share=7,
            video_path=self.tmp_path / "video.mp4",
        )
        card.video_path.write_bytes(b"fake mp4")
        chain = main._build_video_chain(card)

        await main._send_chain_with_video_fallback(
            "aiocqhttp:GroupMessage:123",
            chain,
            card,
            push=False,
        )

        self.assertEqual(1, len(context.sent_chains))
        self.assertIsInstance(context.sent_chains[0].chain[0], str)

    async def test_video_send_failure_marks_destination_cooldown(self) -> None:
        main = plugin.Main.__new__(plugin.Main)
        context = _FailingVideoContext()
        main.context = context
        main.config = {"runtime_settings": {"video_send_failure_cooldown_seconds": 60}}
        main._video_send_failure_until = {}
        card = plugin.VideoCard(
            aid=12,
            bvid="BV1xx411c7mD",
            title="fallback title",
            link="https://www.bilibili.com/video/BV1xx411c7mD",
            up_name="fallback up",
            cover_url="",
            desc="fallback desc",
            duration_seconds=60,
            pub_ts=123456,
            view=1,
            like=2,
            danmaku=3,
            reply=4,
            favorite=5,
            coin=6,
            share=7,
            video_path=self.tmp_path / "video.mp4",
        )
        card.video_path.write_bytes(b"fake mp4")
        chain = main._build_video_chain(card)

        await main._send_chain_with_video_fallback(
            "aiocqhttp:GroupMessage:123",
            chain,
            card,
            push=False,
        )

        self.assertIn("aiocqhttp:GroupMessage:123", main._video_send_failure_until)
        self.assertEqual(2, len(context.sent_chains))
        self.assertEqual("Video", getattr(context.sent_chains[0].chain[0], "type", ""))
        self.assertIsInstance(context.sent_chains[1].chain[0], str)

    async def _noop_async(self, *_args, **_kwargs):
        return None

    async def asyncSetUp(self) -> None:
        import tempfile
        from pathlib import Path

        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    async def asyncTearDown(self) -> None:
        self._temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
