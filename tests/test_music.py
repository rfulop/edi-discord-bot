import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.music import MusicPlayer, YTDLSource, is_url


class MusicHelpersTest(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(MusicPlayer.format_duration(61), "01m 01s")
        self.assertEqual(MusicPlayer.format_duration(3661), "1h 01m 01s")
        self.assertEqual(MusicPlayer.format_duration(None), "durée inconnue")

    def test_url_detection(self):
        self.assertTrue(is_url("https://www.youtube.com/watch?v=abc"))
        self.assertFalse(is_url("musique ambiance taverne"))
        self.assertFalse(is_url("javascript:alert(1)"))


class YTDLSourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_load_metadata_uses_first_valid_entry(self):
        requester = SimpleNamespace(mention="@joueur")
        source = YTDLSource(requester)
        result = {
            "entries": [
                None,
                {
                    "title": "Taverne",
                    "webpage_url": "https://example.test/video",
                    "duration": 123,
                    "url": "https://stream.test/old",
                },
            ]
        }
        with patch("cogs.music.YTDL.extract", AsyncMock(return_value=result)):
            await source.load_metadata("query")
        self.assertEqual(source.title, "Taverne")
        self.assertEqual(source.duration, "02m 03s")
        self.assertEqual(source.webpage_url, "https://example.test/video")

    async def test_refresh_stream_url_reextracts_at_playback_time(self):
        requester = SimpleNamespace(mention="@joueur")
        source = YTDLSource(
            requester,
            webpage_url="https://example.test/video",
            title="Ancien titre",
            duration=1,
        )
        result = {
            "title": "Titre actuel",
            "duration": 125,
            "url": "https://stream.test/fresh",
        }
        with patch("cogs.music.YTDL.extract", AsyncMock(return_value=result)) as extract:
            stream_url = await source.refresh_stream_url()
        extract.assert_awaited_once_with("https://example.test/video")
        self.assertEqual(stream_url, "https://stream.test/fresh")
        self.assertEqual(source.title, "Titre actuel")
        self.assertEqual(source.duration, "02m 05s")


if __name__ == "__main__":
    unittest.main()
