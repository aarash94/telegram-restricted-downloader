"""Offline checks for Spotify/X routing and download boundaries. Run: python test_downloaders.py."""
import asyncio
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import bot


def check_parsers():
    # Wrong hosts/IDs must never become download requests.
    track = "4cOdK2wGLETKBW3PvgPWqT"
    expected = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
    for text in (expected + "?si=abc", f"listen https://open.spotify.com/intl-en/track/{track}",
                 f"spotify:track:{track}"):
        assert bot.spotify_parse(text) == expected, text
    for text in (None, "https://open.spotify.com/playlist/" + track,
                 "https://evilopen.spotify.com/track/" + track,
                 expected + "extra", "https://open.spotify.com/track/short"):
        assert bot.spotify_parse(text) is None, text
    for text in ("https://x.com/name/status/12345?s=20", "https://twitter.com/name/status/12345/video/2",
                 "https://mobile.twitter.com/name/status/12345", "x.com/i/web/status/12345"):
        assert bot.x_parse(text) == "https://x.com/i/status/12345", text
    for text in (None, "https://notx.com/name/status/12345", "https://x.com/name", "https://x.com/name/status/123abc"):
        assert bot.x_parse(text) is None, text


def check_spotify():
    # Network calls are replaced; request isolation, reuse and failure handling are real.
    song = SimpleNamespace(name="Song", artists=["Artist"], duration=123)
    calls = []

    class Spotdl:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.downloader = SimpleNamespace(settings=kwargs["downloader_settings"],
                audio_providers=[], search=lambda song: "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        def search(self, urls):
            assert urls == ["https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"]
            return [song]

    def download(vid, choice, folder, title):
        assert vid == "dQw4w9WgXcQ"
        path = Path(folder, "Artist - Song.m4a")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return str(path)

    with TemporaryDirectory() as tmp, patch.dict(sys.modules, {
            "spotdl": SimpleNamespace(Spotdl=Spotdl),
            "spotdl.utils.metadata": SimpleNamespace(embed_metadata=lambda path, song: None)}), \
            patch.object(bot, "SPOTIFY_CLIENT", None), patch.object(bot, "yt_ffmpeg", return_value={}), \
            patch.object(bot, "yt_download", side_effect=download):
        for name in ("first", "second"):
            folder = os.path.join(tmp, name)
            path, title, artists, duration = bot.spotify_download(
                "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", folder)
            assert Path(path).read_bytes() == b"audio"
            assert Path(path).parent == Path(folder)
            assert (title, artists, duration) == ("Song", "Artist", 123)
        assert len(calls) == 1, "reuse the initialized downloader across requests"
        settings = calls[0]["downloader_settings"]
        assert settings["threads"] == 1 and settings["lyrics_providers"] == []
        assert settings["format"] == "m4a" and settings["bitrate"] == "auto"
        with patch.object(bot.SPOTIFY_CLIENT.downloader, "search", return_value=None):
            try:
                bot.spotify_download("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", tmp)
            except RuntimeError:
                pass
            else:
                raise AssertionError("failed Spotify download must raise")


def check_x():
    # Multi-video metadata must deliver every completed media file in post order.
    with TemporaryDirectory() as tmp:
        for name in ("first.mp4", "second.mp4", "unfinished.mp4.part", "metadata.json"):
            Path(tmp, name).write_bytes(b"video")
        info = {"description": "Caption #tags", "entries": [
            {"requested_downloads": [{"filepath": str(Path(tmp, "second.mp4"))}]},
            {"requested_downloads": [{"filepath": str(Path(tmp, "first.mp4"))}]},
        ]}
        with patch.object(bot.yt_dlp.YoutubeDL, "extract_info", return_value=info):
            files, caption = bot.x_download("https://x.com/i/status/12345", tmp)
            assert [Path(f).name for f in files] == ["second.mp4", "first.mp4"]
            assert caption == "Caption #tags"
        with patch.object(bot.yt_dlp.YoutubeDL, "extract_info", return_value={}):
            try:
                bot.x_download("https://x.com/i/status/12345", tmp)
            except RuntimeError:
                pass
            else:
                raise AssertionError("empty X extraction must raise")
        # X GIFs have no audio; the selector must still accept their MP4 video.
        with bot.yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": "b[ext=mp4]/b"}) as y:
            selected = y.process_ie_result({"id": "gif", "title": "GIF", "extractor": "fixture",
                "formats": [{"format_id": "gif", "ext": "mp4", "vcodec": "h264", "acodec": "none",
                             "url": "https://example.com/gif.mp4"}]}, download=False)
            assert selected["format_id"] == "gif"


def check_delivery():
    # An upload failure must remove the progress message and the partially downloaded file.
    from unittest.mock import AsyncMock

    async def run():
        with TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            os.mkdir("dl")
            try:
                note = SimpleNamespace(delete=AsyncMock())
                event = SimpleNamespace(chat_id=7, reply=AsyncMock(return_value=note), respond=AsyncMock())
                client = SimpleNamespace(send_file=AsyncMock(side_effect=RuntimeError("upload failed")))

                def download(url, folder):
                    path = Path(folder, "song.m4a")
                    path.write_bytes(b"audio")
                    return str(path), "Song", "Artist", 123

                with patch.object(bot, "spotify_download", side_effect=download):
                    try:
                        await bot.send_external(event, client, "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT", None)
                    except RuntimeError as e:
                        assert str(e) == "upload failed"
                    else:
                        raise AssertionError("upload error should propagate")
                assert list(Path("dl").iterdir()) == []
                note.delete.assert_awaited_once()
                event.respond.assert_not_awaited()
                attributes = client.send_file.call_args.kwargs["attributes"][0]
                assert attributes.title == "Song" and attributes.performer == "Artist" and attributes.duration == 123
                # X long captions are split into valid Telegram messages, with the files removed afterward.
                client.send_file.side_effect = None
                note.delete.reset_mock()
                event.reply.reset_mock()
                def x_download(url, folder):
                    path = Path(folder, "video.mp4")
                    path.write_bytes(b"video")
                    return [str(path)], "x" * 8500
                with patch.object(bot, "x_download", side_effect=x_download):
                    await bot.send_external(event, client, None, "https://x.com/i/status/12345")
                assert [len(c.args[0]) for c in event.reply.await_args_list[1:]] == [4000, 4000, 500]
                assert client.send_file.call_args.kwargs["caption"] is None
                assert client.send_file.call_args.kwargs["supports_streaming"]
                assert list(Path("dl").iterdir()) == []
                note.delete.assert_awaited_once()
            finally:
                os.chdir(old)
    asyncio.run(run())


def check_spotify_library():
    """Exercise real spotDL metadata and yt-dlp's file/processor pipeline offline."""
    import shutil
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    from mutagen.mp4 import MP4

    with TemporaryDirectory() as tmp, patch.object(Path, "home", return_value=Path(tmp)):
        from spotdl.types.song import Song
        from spotdl.utils.spotify import SpotifyClient
        from spotdl.providers.audio.ytmusic import YouTubeMusic
        from spotdl.download.downloader import Downloader
        from yt_dlp.postprocessor.ffmpeg import FFmpegExtractAudioPP

        song = Song(name="Song", artists=["Artist"], artist="Artist", genres=[], disc_number=1,
                    disc_count=1, album_name="Album", album_artist="Artist", duration=123,
                    year=2026, date="2026-01-01", track_number=1, tracks_count=1,
                    song_id="4cOdK2wGLETKBW3PvgPWqT", explicit=False, publisher="",
                    url="https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT",
                    isrc=None, cover_url=None, copyright_text=None, album_id="album")
        fixture = Path(tmp, "fixture.m4a")
        ffmpeg = bot.yt_ffmpeg().get("ffmpeg_location") or shutil.which("ffmpeg")
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "sine=duration=0.1",
                        "-c:a", "aac", "-threads", "1", str(fixture)], check=True, timeout=15)

        def audio_response(self, url, download=True, **kwargs):
            assert download and url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            return self.process_ie_result({"id": "dQw4w9WgXcQ", "title": "Fixture",
                "extractor": "fixture", "extractor_key": "Fixture", "webpage_url": url,
                "formats": [{"format_id": "140", "url": fixture.as_uri(), "ext": "m4a",
                             "acodec": "aac", "vcodec": "none", "abr": 128}]}, download=True)

        with patch.object(bot, "SPOTIFY_CLIENT", None), patch.object(SpotifyClient, "_instance", None), \
                patch.object(YouTubeMusic, "_create_client", return_value=SimpleNamespace()), \
                patch.object(Song, "from_url", return_value=song), \
                patch.object(Downloader, "search", return_value="https://www.youtube.com/watch?v=dQw4w9WgXcQ"), \
                patch.object(bot.yt_dlp.YoutubeDL, "extract_info", audio_response), \
                patch.object(bot, "yt_opts", return_value={"quiet": True, "no_warnings": True, "enable_file_urls": True}), \
                patch.object(FFmpegExtractAudioPP, "run_ffmpeg", side_effect=AssertionError("must not re-encode AAC")):
            # Separate workers exercise cached client reuse without a shared worker event loop.
            for name in ("one", "two"):
                with ThreadPoolExecutor(max_workers=1) as worker:
                    path, title, artist, duration = worker.submit(
                        bot.spotify_download, song.url, str(Path(tmp, name))).result(timeout=30)
                audio = MP4(path)
                assert audio["\xa9nam"] == ["Song"] and audio["\xa9ART"] == ["Artist"]
                assert audio["\xa9alb"] == ["Album"]
                assert (title, artist, duration) == ("Song", "Artist", 123)
                assert Path(path).parent == Path(tmp, name)
            client = bot.SPOTIFY_CLIENT
            assert client.downloader.audio_providers[0].audio_handler.params["retries"] == 1
            assert client.downloader.audio_providers[0].audio_handler.params["concurrent_fragment_downloads"] == 1
            def failed_transfer(self, url, **kwargs):
                Path(self.params["outtmpl"]["default"] + ".part").write_bytes(b"partial")
                raise RuntimeError("connection lost")
            with patch.object(bot.yt_dlp.YoutubeDL, "extract_info", failed_transfer), TemporaryDirectory(dir=tmp) as folder:
                try:
                    bot.spotify_download(song.url, folder)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("failed transfer must raise")
            assert not list(Path(tmp).rglob("*.part")), "failed audio files escaped request cleanup"
            client.downloader.loop.close()


if __name__ == "__main__":
    check_parsers()
    check_spotify()
    check_x()
    check_delivery()
    check_spotify_library()
    print("Spotify/X checks passed")
