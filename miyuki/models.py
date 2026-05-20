"""Data models for miyuki."""

from dataclasses import dataclass, field


@dataclass
class MovieInfo:
    """Parsed movie metadata."""

    url: str
    uuid: str
    title: str | None
    available_qualities: list[str] = field(default_factory=list)
    segment_count: int = 0
    cover_url: str | None = None


@dataclass
class DownloadResult:
    """Result of a download operation."""

    movie_url: str
    title: str | None
    output_path: str
    quality: str
    segment_total: int
    segment_downloaded: int
    status: str = "completed"  # "completed" | "failed" | "skipped"
    error: str | None = None
