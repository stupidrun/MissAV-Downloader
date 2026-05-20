"""CLI entry point — preserves original miyuki command-line interface."""

import argparse
import logging
import os
import sys

from miyuki.core import MiyukiService

logger = logging.getLogger("miyuki")

banner = """
 ██████   ██████  ███                        █████       ███ 
░░██████ ██████  ░░░                        ░░███       ░░░  
 ░███░█████░███  ████  █████ ████ █████ ████ ░███ █████ ████ 
 ░███░░███ ░███ ░░███ ░░███ ░███ ░░███ ░███  ░███░░███ ░░███ 
 ░███ ░░░  ░███  ░███  ░███ ░███  ░███ ░███  ░██████░   ░███ 
 ░███      ░███  ░███  ░███ ░███  ░███ ░███  ░███░░███  ░███ 
 █████     █████ █████ ░░███████  ░░████████ ████ █████ █████
░░░░░     ░░░░░ ░░░░░   ░░░░░███   ░░░░░░░░ ░░░░ ░░░░░ ░░░░░ 
                        ███ ░███                             
                       ░░██████                              
                        ░░░░░░                               
"""

RECORD_FILE = "downloaded_urls_miyuki.txt"


def setup_logging():
    """Configure logging for CLI usage."""
    miyuki_logger = logging.getLogger("miyuki")
    miyuki_logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler("miyuki.log")
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "Miyuki - %(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    miyuki_logger.addHandler(file_handler)
    miyuki_logger.addHandler(console_handler)


def already_downloaded(url: str) -> bool:
    """Check if URL was previously downloaded."""
    if not os.path.exists(RECORD_FILE):
        return False
    with open(RECORD_FILE, "r", encoding="utf-8") as f:
        return url in {line.strip() for line in f}


def record_downloaded(url: str):
    """Append URL to download record."""
    with open(RECORD_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")


def check_positive_integer(value: str | None) -> bool:
    if value is None:
        return True
    return value.isdigit() and int(value) > 0


def validate_args(args):
    """Validate CLI arguments, exit on error."""
    sources = [args.urls, args.auth, args.plist, args.search, args.file]
    non_none = sum(1 for s in sources if s is not None)
    if non_none != 1:
        logger.error(
            "Among -urls, -auth, -search, -plist, and -file, exactly one must be specified."
        )
        sys.exit(1)

    if args.auth is not None and len(args.auth) != 2:
        logger.error("The -auth option requires exactly 2 values: email password")
        sys.exit(1)

    if args.file is not None:
        if not os.path.isfile(args.file) or os.path.getsize(args.file) == 0:
            logger.error("The -file option requires a valid non-empty file path.")
            sys.exit(1)

    for name in ("quality", "retry", "delay", "timeout", "limit"):
        val = getattr(args, name, None)
        if not check_positive_integer(val):
            logger.error(f"The -{name} option accepts only positive integers.")
            sys.exit(1)

    if args.ffmpeg or args.ffcover:
        try:
            import subprocess

            subprocess.run(
                ["ffmpeg", "-version"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            logger.error("FFmpeg is required but not found.")
            sys.exit(1)


def execute_download(args):
    """Resolve movie URLs and execute downloads."""
    # Resolve configuration with priority: CLI > env > default
    quality = args.quality or os.environ.get("MIYUKI_QUALITY", "720")
    output_dir = args.output or os.environ.get("MIYUKI_OUTPUT", "movies_folder_miyuki")
    retry = int(args.retry) if args.retry else int(os.environ.get("MIYUKI_RETRY", "5"))
    delay = int(args.delay) if args.delay else int(os.environ.get("MIYUKI_DELAY", "2"))
    timeout = (
        int(args.timeout)
        if args.timeout
        else int(os.environ.get("MIYUKI_TIMEOUT", "10"))
    )

    use_ffmpeg = args.ffmpeg or args.ffcover
    download_cover = args.cover or args.ffcover

    service = MiyukiService(
        output_dir=output_dir,
        quality=quality,
        retry=retry,
        delay=delay,
        timeout=timeout,
        proxy=args.proxy,
    )

    # Resolve movie URLs from the chosen source
    movie_urls: list[str] = []

    if args.urls is not None:
        movie_urls = args.urls

    elif args.auth is not None:
        movie_urls = service.login_and_get_collections(args.auth[0], args.auth[1])
        logger.info(f"Favorited videos: {len(movie_urls)} movies")

    elif args.plist is not None:
        limit = int(args.limit) if args.limit else None
        movie_urls = service.get_playlist_urls(args.plist, limit=limit)
        logger.info(f"Playlist videos: {len(movie_urls)} movies")

    elif args.search is not None:
        results = service.search(args.search)
        if results:
            logger.info(f"Search '{args.search}' found: {results[0]}")
            movie_urls = [results[0]]
        else:
            logger.error(f"Search failed, no results for: {args.search}")
            sys.exit(1)

    elif args.file is not None:
        with open(args.file, "r", encoding="utf-8") as f:
            movie_urls = [line.strip() for line in f if line.strip()]
        logger.info(f"File URLs: {len(movie_urls)} movies")

    if not movie_urls:
        logger.error("No URLs found.")
        sys.exit(1)

    for url in movie_urls:
        logger.info(url)

    # Download each movie
    for url in movie_urls:
        if already_downloaded(url):
            logger.info(f"{url} already downloaded, skipping.")
            continue

        try:
            logger.info(f"Processing: {url}")
            result = service.download(
                movie_url=url,
                use_ffmpeg=use_ffmpeg,
                download_cover=download_cover,
                use_title_as_filename=args.title,
                cover_as_preview=args.ffcover,
            )
            record_downloaded(url)
            logger.info(
                f"Complete: {result.output_path} "
                f"({result.segment_downloaded}/{result.segment_total} segments)"
            )
            print()
        except Exception as e:
            logger.error(f"Failed to download {url}: {e}")


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description='A tool for downloading videos from the "MissAV" website.\n'
        "\n"
        "Main Options:\n"
        "Use the -urls   option to specify the video URLs to download.\n"
        "Use the -auth   option to specify the username and password to download the videos collected by the account.\n"
        "Use the -plist  option to specify the public playlist URL to download all videos in the list.\n"
        "Use the -search option to search for movie by serial number and download it.\n"
        "Use the -file   option to download all URLs in the file. ( Each line is a URL )\n"
        "\n"
        "Additional Options:\n"
        "Use the -limit   option to limit the number of downloads. (Only works with the -plist option.)\n"
        "Use the -proxy   option to configure http proxy server ip and port.\n"
        "Use the -output  option to specify the output directory.\n"
        "Use the -ffmpeg  option to get the best video quality. ( Recommend! )\n"
        "Use the -cover   option to save the cover when downloading the video\n"
        "Use the -ffcover option to set the cover as the video preview (ffmpeg required)\n"
        "Use the -noban   option to turn off the miyuki banner when downloading the video\n"
        "Use the -title   option to use the full title as the movie file name\n"
        "Use the -quality option to specify the movie resolution (360, 480, 720, 1080...)\n"
        "                 Priority: CLI arg > env MIYUKI_QUALITY > default 720\n"
        "Use the -retry   option to specify the number of retries for downloading segments\n"
        "Use the -delay   option to specify the delay before retry ( seconds )\n"
        "Use the -timeout option to specify the timeout for segment download ( seconds )\n",
        epilog="Examples:\n"
        '  miyuki -plist "https://missav.live/search/JULIA?filters=uncensored-leak&sort=saved" -limit 50 -ffmpeg\n'
        '  miyuki -plist "https://missav.live/search/JULIA?filters=individual&sort=views" -limit 20 -ffmpeg\n'
        '  miyuki -plist "https://missav.live/dm132/actresses/JULIA" -limit 20 -ffmpeg -cover\n'
        '  miyuki -plist "https://missav.live/playlists/ewzoukev" -ffmpeg -proxy localhost:7890\n'
        "  miyuki -urls https://missav.live/sw-950 https://missav.live/dandy-917\n"
        "  miyuki -urls https://missav.live/sw-950 -proxy localhost:7890\n"
        "  miyuki -auth miyuki@gmail.com miyukiQAQ -ffmpeg\n"
        "  miyuki -file /home/miyuki/url.txt -ffmpeg\n"
        "  miyuki -search sw-950 -ffcover\n",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "-urls", nargs="+", required=False, metavar="", help="Movie URLs"
    )
    parser.add_argument(
        "-auth", nargs="+", required=False, metavar="", help="Email and password"
    )
    parser.add_argument(
        "-plist", type=str, required=False, metavar="", help="Public playlist URL"
    )
    parser.add_argument(
        "-limit", type=str, required=False, metavar="", help="Limit downloads"
    )
    parser.add_argument(
        "-search", type=str, required=False, metavar="", help="Movie serial number"
    )
    parser.add_argument(
        "-file", type=str, required=False, metavar="", help="File with URLs"
    )
    parser.add_argument(
        "-proxy", type=str, required=False, metavar="", help="HTTP(S) proxy"
    )
    parser.add_argument(
        "-output", type=str, required=False, metavar="", help="Output directory"
    )
    parser.add_argument(
        "-ffmpeg", action="store_true", required=False, help="Use ffmpeg to merge"
    )
    parser.add_argument(
        "-cover", action="store_true", required=False, help="Download cover"
    )
    parser.add_argument(
        "-ffcover", action="store_true", required=False, help="Cover as preview"
    )
    parser.add_argument(
        "-noban", action="store_true", required=False, help="Hide banner"
    )
    parser.add_argument(
        "-title", action="store_true", required=False, help="Use title as filename"
    )
    parser.add_argument(
        "-quality",
        type=str,
        required=False,
        metavar="",
        help="Resolution (720, 1080...)",
    )
    parser.add_argument(
        "-retry", type=str, required=False, metavar="", help="Retry count"
    )
    parser.add_argument(
        "-delay", type=str, required=False, metavar="", help="Retry delay (seconds)"
    )
    parser.add_argument(
        "-timeout",
        type=str,
        required=False,
        metavar="",
        help="Request timeout (seconds)",
    )

    args = parser.parse_args()

    logger.info(args)
    validate_args(args)

    if not args.noban:
        print(banner)

    execute_download(args)


if __name__ == "__main__":
    main()
