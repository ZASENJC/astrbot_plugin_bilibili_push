from __future__ import annotations

import unittest

from tests.conftest import plugin


class UrlNormalizationTest(unittest.TestCase):
    def test_normalize_cover_url_allows_known_bilibili_cdn(self) -> None:
        self.assertEqual(
            "https://i0.hdslb.com/bfs/archive/cover.jpg",
            plugin.normalize_cover_url("//i0.hdslb.com/bfs/archive/cover.jpg"),
        )

    def test_normalize_cover_url_rejects_private_and_untrusted_hosts(self) -> None:
        self.assertEqual("", plugin.normalize_cover_url("http://127.0.0.1/admin.png"))
        self.assertEqual("", plugin.normalize_cover_url("http://169.254.169.254/latest/meta-data"))
        self.assertEqual("", plugin.normalize_cover_url("https://example.com/cover.jpg"))
        self.assertEqual("", plugin.normalize_cover_url("https://i0.hdslb.com:8443/cover.jpg"))


if __name__ == "__main__":
    unittest.main()
