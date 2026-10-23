# Authored By Certified Coders © 2026
# System: Spotify API (Non-Blocking Async Thread + Merged YouTube Innertube Engine)

import asyncio
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

import config
from AnnieXMedia.platforms.Youtube import YouTube


class SpotifyAPI:
    def __init__(self):
        self.regex = r"^https:\/\/open\.spotify\.com\/.+"
        self.client_id = config.SPOTIFY_CLIENT_ID
        self.client_secret = config.SPOTIFY_CLIENT_SECRET

        if self.client_id and self.client_secret:
            self.client_credentials_manager = SpotifyClientCredentials(
                self.client_id, self.client_secret
            )
            self.spotify = spotipy.Spotify(
                client_credentials_manager=self.client_credentials_manager
            )
        else:
            self.spotify = None

    async def valid(self, link: str) -> bool:
        return bool(re.search(self.regex, link or ""))

    async def track(self, link: str):
        """
        جلب بيانات التراك من سبوتيفاي بدون تجميد البوت (to_thread)
        ودمج البحث والرابط المباشر عبر محرك YouTube.track السريع (80ms)
        """
        if not self.spotify:
            raise RuntimeError("Spotify credentials not configured")

        try:
            track = await asyncio.to_thread(self.spotify.track, link)
        except Exception:
            return None, None

        info = track.get("name", "")
        for artist in track.get("artists", []):
            fetched = f' {artist.get("name", "")}'
            if "Various Artists" not in fetched:
                info += fetched

        info = info.strip()
        if not info:
            return None, None

        try:
            details, vidid = await YouTube.track(info)
            if not details or not vidid or details.get("title") == "Unknown":
                return None, None
            return details, vidid
        except Exception:
            return None, None

    async def download(self, link: str):
        details, vidid = await self.track(link)
        if not details or not vidid:
            return False, False

        yt_link = details["link"]
        title = details["title"]

        try:
            path, _ = await YouTube.download(
                yt_link,
                mystic=None,
                videoid=vidid,
                songaudio=True,
                title=title,
            )
        except Exception:
            return False, False

        if path:
            details["filepath"] = path
            return details, path

        return False, False

    async def playlist(self, url):
        if not self.spotify:
            raise RuntimeError("Spotify credentials not configured")
        try:
            playlist = await asyncio.to_thread(self.spotify.playlist, url)
            playlist_id = playlist["id"]
            results = []
            for item in playlist.get("tracks", {}).get("items", []):
                music_track = item.get("track")
                if not music_track:
                    continue
                info = music_track.get("name", "")
                for artist in music_track.get("artists", []):
                    fetched = f' {artist.get("name", "")}'
                    if "Various Artists" not in fetched:
                        info += fetched
                if info.strip():
                    results.append(info.strip())
            return results, playlist_id
        except Exception:
            return [], None

    async def album(self, url):
        if not self.spotify:
            raise RuntimeError("Spotify credentials not configured")
        try:
            album = await asyncio.to_thread(self.spotify.album, url)
            album_id = album["id"]
            results = []
            for item in album.get("tracks", {}).get("items", []):
                info = item.get("name", "")
                for artist in item.get("artists", []):
                    fetched = f' {artist.get("name", "")}'
                    if "Various Artists" not in fetched:
                        info += fetched
                if info.strip():
                    results.append(info.strip())
            return results, album_id
        except Exception:
            return [], None

    async def artist(self, url):
        if not self.spotify:
            raise RuntimeError("Spotify credentials not configured")
        try:
            artistinfo, artisttoptracks = await asyncio.gather(
                asyncio.to_thread(self.spotify.artist, url),
                asyncio.to_thread(self.spotify.artist_top_tracks, url),
            )
            artist_id = artistinfo["id"]
            results = []
            for item in artisttoptracks.get("tracks", []):
                info = item.get("name", "")
                for artist in item.get("artists", []):
                    fetched = f' {artist.get("name", "")}'
                    if "Various Artists" not in fetched:
                        info += fetched
                if info.strip():
                    results.append(info.strip())
            return results, artist_id
        except Exception:
            return [], None
