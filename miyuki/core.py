"""Core service layer — stateless, reusable download logic."""

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from typing import Callable

from miyuki.client import (
    COVER_URL_PREFIX,
    MISSAV_DOMAIN,
    VIDEO_M3U8_PREFIX,
    DEFAULT_RETRY,
    DEFAULT_DELAY,
    DEFAULT_TIMEOUT,
    MiyukiClient,
)
from miyuki.models import DownloadResult, MovieInfo

logger = logging.getLogger("miyuki")

# Regex patterns for parsing
MATCH_UUID_PATTERN = r"m3u8\|([a-f0-9\|]+)\|com\|surrit\|https\|video"
MATCH_TITLE_PATTERN = r"<title>([^\"]+)</title>"
RESOLUTION_PATTERN = r"RESOLUTION=(\d+)x(\d+)"
HREF_REGEX_PUBLIC_PLAYLIST = r'<a href="([^"]+)" alt="'
HREF_REGEX_NEXT_PAGE = r'<a href="([^"]+)" rel="next"'
PLAYLIST_SUFFIX = "/playlist.m3u8"


class ThreadSafeCounter:
    def __init__(self):
        self._count = 0
        self._lock = threading.Lock()

    def increment_and_get(self) -> int:
        with self._lock:
            self._count += 1
            return self._count

    def reset(self):
        with self._lock:
            self._count = 0

    def get(self) -> int:
        with self._lock:
            return self._count


class MiyukiService:
    """Core download service. All paths are explicit, no global state."""

    def __init__(
        self,
        output_dir: str = "./downloads",
        quality: str = "720",
        num_threads: int | None = None,
        retry: int = DEFAULT_RETRY,
        delay: int = DEFAULT_DELAY,
        timeout: int = DEFAULT_TIMEOUT,
        proxy: str | None = None,
    ):
        self.output_dir = os.path.abspath(output_dir)
        self.quality = quality
        self.num_threads = num_threads or os.cpu_count() or 4
        self.retry = retry
        self.delay = delay
        self.timeout = timeout
        self.client = MiyukiClient(proxy=proxy)
        self._counter = ThreadSafeCounter()

    # ─── Public API ───────────────────────────────────────────────────────

    def get_movie_info(self, url: str) -> MovieInfo:
        """Fetch movie metadata without downloading."""
        html = self.client.get(url).text
        uuid = self._extract_uuid(html)
        title = self._extract_title(html)
        movie_name = url.rstrip("/").split("/")[-1]

        playlist_url = VIDEO_M3U8_PREFIX + uuid + PLAYLIST_SUFFIX
        playlist = self.client.get(playlist_url).text
        qualities = self._parse_available_qualities(playlist)

        # Get segment count for the highest quality
        _, resolution_url = self._select_quality(playlist, self.quality)
        video_m3u8_url = VIDEO_M3U8_PREFIX + uuid + "/" + resolution_url
        video_m3u8 = self.client.get(video_m3u8_url).text
        segment_count = self._parse_segment_count(video_m3u8)

        cover_url = f"{COVER_URL_PREFIX}{movie_name}/cover-n.jpg"

        return MovieInfo(
            url=url,
            uuid=uuid,
            title=title,
            available_qualities=qualities,
            segment_count=segment_count,
            cover_url=cover_url,
        )

    def search(self, keyword: str) -> list[str]:
        """Search for movies by keyword, return list of URLs."""
        search_url = f"{MISSAV_DOMAIN}/search/{keyword}"
        search_regex = r'<a href="([^"]+)" alt="' + re.escape(keyword) + '" >'
        html = self.client.get(search_url).text
        matches = re.findall(pattern=search_regex, string=html)
        return list(set(matches))

    def get_playlist_urls(
        self, playlist_url: str, limit: int | None = None, cookie: dict | None = None
    ) -> list[str]:
        """Get all movie URLs from a public playlist (with pagination)."""
        movie_urls: list[str] = []
        current_url: str | None = playlist_url

        while current_url:
            html = self.client.get(current_url, cookies=cookie).text
            matches = re.findall(pattern=HREF_REGEX_PUBLIC_PLAYLIST, string=html)
            for url in set(matches):
                movie_urls.append(url)
                logger.info(f"Movie {len(movie_urls)} url: {url}")
                if limit is not None and len(movie_urls) >= limit:
                    return movie_urls

            next_page = re.findall(pattern=HREF_REGEX_NEXT_PAGE, string=html)
            if len(next_page) == 1:
                current_url = next_page[0].replace("&amp;", "&")
            else:
                break

        return movie_urls

    def login_and_get_collections(self, email: str, password: str) -> list[str]:
        """Login and return all saved/favorited movie URLs."""
        response = self.client.post(
            f"{MISSAV_DOMAIN}/api/login",
            data={"email": email, "password": password},
        )
        if response.status_code != 200:
            raise RuntimeError("Login failed: bad status code")

        cookie = response.cookies.get_dict()
        if "user_uuid" not in cookie:
            raise RuntimeError("Login failed: no user_uuid in cookies")

        logger.info(f"Logged in, user uuid: {cookie['user_uuid']}")
        return self.get_playlist_urls(f"{MISSAV_DOMAIN}/saved", cookie=cookie)

    def download(
        self,
        movie_url: str,
        use_ffmpeg: bool = True,
        download_cover: bool = True,
        use_title_as_filename: bool = False,
        cover_as_preview: bool = False,
        quality: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> DownloadResult:
        """
        Download a single movie.

        Args:
            movie_url: URL of the movie page.
            use_ffmpeg: Use ffmpeg for merging (better quality).
            download_cover: Download cover image.
            use_title_as_filename: Rename output to full title.
            cover_as_preview: Embed cover as video preview (requires ffmpeg).
            quality: Override instance quality for this download.
            progress_callback: Called with (current, total) during download.

        Returns:
            DownloadResult with status and file path.
        """
        effective_quality = quality or self.quality
        movie_name = movie_url.rstrip("/").split("/")[-1]

        # Fetch page and extract UUID
        html = self.client.get(movie_url).text
        uuid = self._extract_uuid(html)
        title = self._extract_title(html)

        # Get playlist and select quality
        playlist_url = VIDEO_M3U8_PREFIX + uuid + PLAYLIST_SUFFIX
        playlist = self.client.get(playlist_url).text
        final_quality, resolution_url = self._select_quality(
            playlist, effective_quality
        )

        resolution = resolution_url.split("/")[0]
        final_file_name = f"{movie_name}_{final_quality}"

        # Get segment count
        video_m3u8_url = VIDEO_M3U8_PREFIX + uuid + "/" + resolution_url
        video_m3u8 = self.client.get(video_m3u8_url).text
        segment_count = self._parse_segment_count(video_m3u8)

        # Prepare directories
        segments_dir = os.path.join(self.output_dir, movie_name)
        os.makedirs(segments_dir, exist_ok=True)

        # Download cover
        if download_cover:
            self._download_cover(movie_name)

        # Download segments
        self._counter.reset()
        self._download_segments(
            uuid=uuid,
            resolution=resolution,
            movie_name=movie_name,
            segment_count=segment_count,
            progress_callback=progress_callback,
        )
        segments_downloaded = self._counter.get()

        # Merge to mp4
        output_path = os.path.join(self.output_dir, f"{final_file_name}.mp4")
        if use_ffmpeg and self._ffmpeg_available():
            self._merge_with_ffmpeg(
                movie_name, segment_count, final_file_name, cover_as_preview
            )
        else:
            self._merge_binary(movie_name, segment_count, final_file_name)

        # Rename to title if requested
        if use_title_as_filename and title:
            titled_path = os.path.join(self.output_dir, f"{title}.mp4")
            os.rename(output_path, titled_path)
            output_path = titled_path

        # Cleanup segments directory
        shutil.rmtree(segments_dir, ignore_errors=True)

        logger.info(f"Download complete: {output_path}")
        return DownloadResult(
            movie_url=movie_url,
            title=title,
            output_path=output_path,
            quality=final_quality,
            segment_total=segment_count,
            segment_downloaded=segments_downloaded,
            status="completed",
        )

    # ─── Private helpers ──────────────────────────────────────────────────

    def _extract_uuid(self, html: str) -> str:
        match = re.search(MATCH_UUID_PATTERN, html)
        if not match:
            raise ValueError("Failed to extract video UUID from page HTML")
        parts = match.group(1).split("|")
        uuid = "-".join(parts[::-1])
        logger.info(f"Extracted UUID: {uuid}")
        return uuid

    def _extract_title(self, html: str) -> str | None:
        match = re.search(MATCH_TITLE_PATTERN, html)
        if match:
            title = match.group(1)
            title = title.replace("&#039;", "'")
            title = title.replace("/", "_")
            title = title.replace("\\", "_")
            return title
        return None

    def _parse_available_qualities(self, playlist: str) -> list[str]:
        matches = re.findall(pattern=RESOLUTION_PATTERN, string=playlist)
        return [f"{m[1]}p" for m in matches]

    def _select_quality(self, playlist: str, quality: str) -> tuple[str, str]:
        """Select the best matching quality, returns (quality_label, resolution_url)."""
        try:
            matches = re.findall(pattern=RESOLUTION_PATTERN, string=playlist)
            quality_map = {}
            quality_list = []
            m3u8_suffix = "/video.m3u8"

            for match in matches:
                quality_map[match[1]] = match[0]
                quality_list.append(match[1])

            if not quality_list:
                raise ValueError("No qualities found in playlist")

            # Find closest resolution
            target = int(quality)
            closest = min(quality_list, key=lambda x: abs(int(x) - target))

            url_type_x = quality_map[closest] + "x" + closest + m3u8_suffix
            url_type_p = closest + "p" + m3u8_suffix

            if url_type_x in playlist:
                return closest + "p", url_type_x
            elif url_type_p in playlist:
                return closest + "p", url_type_p
            else:
                return quality_list[-1] + "p", self._find_last_non_empty_line(playlist)
        except Exception:
            resolution_url = self._find_last_non_empty_line(playlist)
            final_quality = resolution_url.split("/")[0]
            return final_quality, resolution_url

    def _parse_segment_count(self, video_m3u8: str) -> int:
        """Parse the total number of segments from video.m3u8."""
        lines = video_m3u8.splitlines()
        # The second-to-last line contains the last segment filename
        last_segment_line = lines[-2]
        match = re.search(r"(\d+)", last_segment_line)
        if not match:
            raise ValueError("Failed to parse segment count from video.m3u8")
        return int(match.group(0)) + 1  # 0-indexed, so total = max + 1

    def _find_last_non_empty_line(self, text: str) -> str:
        for line in reversed(text.splitlines()):
            if line.strip():
                return line
        raise ValueError("No non-empty lines found")

    def _download_cover(self, movie_name: str):
        """Download cover image."""
        try:
            cover_url = f"{COVER_URL_PREFIX}{movie_name}/cover-n.jpg"
            content = self.client.get_with_retry(cover_url, retry=3)
            if content:
                cover_path = os.path.join(self.output_dir, f"{movie_name}-cover.jpg")
                with open(cover_path, "wb") as f:
                    f.write(content)
        except Exception as e:
            logger.error(f"Failed to download cover for {movie_name}: {e}")

    def _download_segments(
        self,
        uuid: str,
        resolution: str,
        movie_name: str,
        segment_count: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ):
        """Download all video segments using multiple threads."""
        intervals = self._split_into_intervals(segment_count, self.num_threads)
        threads = []

        for start, end in intervals:
            t = threading.Thread(
                target=self._segment_download_worker,
                args=(
                    start,
                    end,
                    uuid,
                    resolution,
                    movie_name,
                    segment_count,
                    progress_callback,
                ),
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _segment_download_worker(
        self,
        start: int,
        end: int,
        uuid: str,
        resolution: str,
        movie_name: str,
        segment_count: int,
        progress_callback: Callable[[int, int], None] | None,
    ):
        """Worker thread: download a range of segments."""
        for i in range(start, end):
            url = f"{VIDEO_M3U8_PREFIX}{uuid}/{resolution}/video{i}.jpeg"
            content = self.client.get_with_retry(
                url, retry=self.retry, delay=self.delay, timeout=self.timeout
            )
            if content is None:
                continue

            file_path = os.path.join(self.output_dir, movie_name, f"video{i}.jpeg")
            with open(file_path, "wb") as f:
                f.write(content)

            current = self._counter.increment_and_get()
            if progress_callback:
                progress_callback(current, segment_count)
            else:
                self._default_progress(current, segment_count)

    def _default_progress(self, current: int, total: int):
        """Default CLI progress bar."""
        bar_length = 50
        progress = current / total
        block = int(round(bar_length * progress))
        text = f"\rProgress: [{'#' * block + '-' * (bar_length - block)}] {current}/{total}"
        sys.stdout.write(text)
        sys.stdout.flush()

    def _split_into_intervals(self, total: int, n: int) -> list[tuple[int, int]]:
        """Split [0, total) into n roughly equal intervals."""
        interval_size = total // n
        remainder = total % n
        intervals = [(i * interval_size, (i + 1) * interval_size) for i in range(n)]
        intervals[-1] = (intervals[-1][0], intervals[-1][1] + remainder)
        return intervals

    def _ffmpeg_available(self) -> bool:
        """Check if ffmpeg is installed."""
        try:
            subprocess.run(
                ["ffmpeg", "-version"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def _merge_with_ffmpeg(
        self,
        movie_name: str,
        segment_count: int,
        final_file_name: str,
        cover_as_preview: bool,
    ):
        """Merge segments using ffmpeg concat demuxer."""
        segments_dir = os.path.join(self.output_dir, movie_name)
        input_list_path = os.path.join(
            self.output_dir, f"{movie_name}_ffmpeg_input.txt"
        )
        output_path = os.path.join(self.output_dir, f"{final_file_name}.mp4")
        cover_path = os.path.join(self.output_dir, f"{movie_name}-cover.jpg")

        # Generate concat input file
        downloaded = 0
        with open(input_list_path, "w") as f:
            for i in range(segment_count):
                seg_path = os.path.join(segments_dir, f"video{i}.jpeg")
                if os.path.exists(seg_path):
                    downloaded += 1
                    f.write(f"file '{seg_path}'\n")

        logger.info(
            f"Segments: {downloaded}/{segment_count} "
            f"({downloaded / segment_count:.1%} complete)"
        )

        # Build ffmpeg command
        if cover_as_preview and os.path.exists(cover_path):
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                input_list_path,
                "-i",
                cover_path,
                "-map",
                "0",
                "-map",
                "1",
                "-c",
                "copy",
                "-disposition:v:1",
                "attached_pic",
                output_path,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                input_list_path,
                "-c",
                "copy",
                output_path,
            ]

        try:
            logger.info("FFmpeg merging...")
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            logger.info("FFmpeg merge completed.")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg failed for {movie_name}: {e}")
        finally:
            # Cleanup input list
            if os.path.exists(input_list_path):
                os.remove(input_list_path)

    def _merge_binary(self, movie_name: str, segment_count: int, final_file_name: str):
        """Merge segments by binary concatenation (no ffmpeg needed)."""
        segments_dir = os.path.join(self.output_dir, movie_name)
        output_path = os.path.join(self.output_dir, f"{final_file_name}.mp4")
        saved = 0

        with open(output_path, "wb") as outfile:
            for i in range(segment_count):
                seg_path = os.path.join(segments_dir, f"video{i}.jpeg")
                try:
                    with open(seg_path, "rb") as infile:
                        outfile.write(infile.read())
                        saved += 1
                except FileNotFoundError:
                    continue

        logger.info(
            f"Binary merge complete: {saved}/{segment_count} "
            f"({saved / segment_count:.1%})"
        )
