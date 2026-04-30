import re
import urllib.parse
import urllib.request
import http.cookiejar

import yt_dlp
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import logging

from ..config import settings
from ..models import MediaInfo, FormatInfo

logger = logging.getLogger(__name__)

# Sites that typically require cookies for extraction
_COOKIE_REQUIRED_PATTERNS = [
    (r'facebook\.com|fb\.watch', 'Facebook'),
    (r'instagram\.com', 'Instagram'),
]


class YTDLPWrapper:
    """Wrapper for yt-dlp operations."""

    def __init__(self, cookies_file: Optional[Path] = None):
        self.cookies_file = cookies_file or settings.cookies_dir / "cookies.txt"

    def _get_base_opts(self) -> Dict[str, Any]:
        """Get base yt-dlp options."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
        }

        # Add cookies if file exists
        if self.cookies_file.exists():
            opts["cookiefile"] = str(self.cookies_file)

        # Impersonate a browser to bypass TLS fingerprint checks
        # (needed for Facebook, Instagram, and other sites that block non-browser requests)
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget.from_str("chrome")
        except (ImportError, Exception):
            pass

        return opts

    def _needs_cookies(self, url: str) -> Optional[str]:
        """Check if URL is from a site that typically requires cookies."""
        for pattern, name in _COOKIE_REQUIRED_PATTERNS:
            if re.search(pattern, url):
                return name
        return None

    def _get_instaloader(self):
        """Create an instaloader instance with cookies loaded."""
        import instaloader

        L = instaloader.Instaloader(
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            post_metadata_txt_pattern='',
        )

        if self.cookies_file.exists():
            try:
                cookie_jar = http.cookiejar.MozillaCookieJar()
                cookie_jar.load(str(self.cookies_file), ignore_discard=True, ignore_expires=True)
                L.context._session.cookies.update(cookie_jar)
            except Exception as e:
                logger.warning(f"Could not load cookies for instaloader: {e}")

        return L

    @staticmethod
    def _extract_instagram_shortcode(url: str) -> Optional[str]:
        """Extract shortcode from an Instagram URL."""
        match = re.search(r'instagram\.com/(?:p|reel|reels)/([A-Za-z0-9_-]+)', url)
        return match.group(1) if match else None

    def _extract_instagram_info(self, url: str) -> MediaInfo:
        """Extract media info from Instagram using instaloader."""
        import instaloader

        shortcode = self._extract_instagram_shortcode(url)
        if not shortcode:
            raise ValueError("Could not extract Instagram shortcode from URL")

        L = self._get_instaloader()

        try:
            post = instaloader.Post.from_shortcode(L.context, shortcode)
        except Exception as e:
            raise ValueError(f"Failed to get Instagram post: {e}")

        caption = post.caption or ""
        title = caption.split('\n')[0][:100] if caption else shortcode
        uploader = post.owner_username if hasattr(post, 'owner_username') else None
        upload_date = post.date_utc.strftime("%Y%m%d") if hasattr(post, 'date_utc') and post.date_utc else None
        thumbnail = post.url

        # Carousel / sidecar post
        if post.typename == "GraphSidecar":
            entries = []
            for idx, node in enumerate(post.get_sidecar_nodes()):
                if node.is_video:
                    entries.append(MediaInfo(
                        id=f"{shortcode}_{idx}",
                        title=f"{title} ({idx + 1})",
                        thumbnail=node.display_url,
                        uploader=uploader,
                        upload_date=upload_date,
                        webpage_url=url,
                        extractor="Instagram",
                        media_type="video",
                        source_url=node.video_url,
                        duration=None,
                    ))
                else:
                    entries.append(MediaInfo(
                        id=f"{shortcode}_{idx}",
                        title=f"{title} ({idx + 1})",
                        thumbnail=node.display_url,
                        uploader=uploader,
                        upload_date=upload_date,
                        webpage_url=url,
                        extractor="Instagram",
                        media_type="image",
                        source_url=node.display_url,
                        duration=None,
                    ))

            return MediaInfo(
                id=shortcode,
                title=title,
                description=caption[:300] if caption else None,
                thumbnail=thumbnail,
                uploader=uploader,
                upload_date=upload_date,
                webpage_url=url,
                extractor="Instagram",
                media_type="video",
                entries=entries,
                duration=None,
            )

        # Single image or video
        if post.is_video:
            return MediaInfo(
                id=shortcode,
                title=title,
                description=caption[:300] if caption else None,
                thumbnail=thumbnail,
                uploader=uploader,
                upload_date=upload_date,
                webpage_url=url,
                extractor="Instagram",
                media_type="video",
                source_url=post.video_url,
                duration=int(post.video_duration) if hasattr(post, 'video_duration') and post.video_duration else None,
            )

        # Single image
        return MediaInfo(
            id=shortcode,
            title=title,
            description=caption[:300] if caption else None,
            thumbnail=thumbnail,
            uploader=uploader,
            upload_date=upload_date,
            webpage_url=url,
            extractor="Instagram",
            media_type="image",
            source_url=post.url,
            duration=None,
        )

    def extract_info(self, url: str) -> MediaInfo:
        """Extract media information from URL."""
        opts = self._get_base_opts()
        opts["extract_flat"] = False
        has_cookies = "cookiefile" in opts

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)

                if info is None:
                    raise ValueError("Could not extract info from URL")

                return self._parse_info(info)

            except yt_dlp.utils.DownloadError as e:
                logger.error(f"yt-dlp extraction failed: {e}")

                # Fallback to instaloader for Instagram URLs
                if re.search(r'instagram\.com', url):
                    logger.info("Falling back to instaloader for Instagram extraction")
                    try:
                        return self._extract_instagram_info(url)
                    except Exception as insta_err:
                        logger.error(f"Instaloader fallback also failed: {insta_err}")
                        # If no cookies and instaloader also failed, suggest cookies
                        if not has_cookies:
                            raise ValueError(
                                "Instagram requires cookies to download this content. "
                                "Please go to Settings and upload a cookies.txt file from a "
                                "browser where you are logged into Instagram."
                            )
                        raise ValueError(f"Failed to extract Instagram info: {insta_err}")

                site_name = self._needs_cookies(url)
                if site_name and not has_cookies:
                    raise ValueError(
                        f"{site_name} requires cookies to download this content. "
                        f"Please go to Settings and upload a cookies.txt file from a "
                        f"browser where you are logged into {site_name}."
                    )
                raise ValueError(f"Failed to extract info: {str(e)}")

    _IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff"}

    def _parse_info(self, info: Dict[str, Any]) -> MediaInfo:
        """Parse yt-dlp info dict into MediaInfo model, handling playlists/carousels."""
        entries = info.get("entries")
        if entries is not None:
            # Playlist or carousel — parse each entry
            parsed_entries = []
            entries_list = list(entries)  # entries can be a generator
            for entry in entries_list:
                if entry is not None:
                    parsed_entries.append(self._parse_single_info(entry))

            if len(parsed_entries) == 1:
                # Single-entry playlist, just return the entry directly
                return parsed_entries[0]

            # Return top-level info with entries populated
            return MediaInfo(
                id=info.get("id", ""),
                title=info.get("title", "Unknown"),
                description=info.get("description"),
                thumbnail=info.get("thumbnail") or (parsed_entries[0].thumbnail if parsed_entries else None),
                duration=None,
                uploader=info.get("uploader") or (parsed_entries[0].uploader if parsed_entries else None),
                upload_date=info.get("upload_date"),
                view_count=info.get("view_count"),
                webpage_url=info.get("webpage_url", ""),
                extractor=info.get("extractor", ""),
                formats=[],
                media_type="video",  # mixed carousel, determined per-entry
                entries=parsed_entries,
            )

        return self._parse_single_info(info)

    def _parse_single_info(self, info: Dict[str, Any]) -> MediaInfo:
        """Parse a single yt-dlp info dict into MediaInfo model."""
        formats = []
        best_video = None
        best_audio = None
        best_video_height = 0
        best_audio_bitrate = 0
        has_any_video = False

        for fmt in info.get("formats", []):
            has_video = fmt.get("vcodec", "none") != "none"
            has_audio = fmt.get("acodec", "none") != "none"

            if has_video:
                has_any_video = True

            # Determine resolution string
            height = fmt.get("height")
            width = fmt.get("width")
            resolution = None
            if height and width:
                resolution = f"{width}x{height}"
            elif height:
                resolution = f"{height}p"

            format_info = FormatInfo(
                format_id=fmt.get("format_id", ""),
                ext=fmt.get("ext", ""),
                resolution=resolution,
                filesize=fmt.get("filesize"),
                filesize_approx=fmt.get("filesize_approx"),
                fps=fmt.get("fps"),
                vcodec=fmt.get("vcodec"),
                acodec=fmt.get("acodec"),
                abr=fmt.get("abr"),
                vbr=fmt.get("vbr"),
                format_note=fmt.get("format_note"),
                quality_label=self._get_quality_label(fmt),
                has_video=has_video,
                has_audio=has_audio,
            )
            formats.append(format_info)

            # Track best video format
            if has_video and height and height > best_video_height:
                best_video_height = height
                best_video = format_info

            # Track best audio format
            abr = fmt.get("abr") or 0
            if has_audio and not has_video and abr > best_audio_bitrate:
                best_audio_bitrate = abr
                best_audio = format_info

        # Detect image media type
        media_type = "video"
        source_url = None
        if not has_any_video:
            ext = info.get("ext", "")
            raw_url = info.get("url", "")
            if ext.lower() in self._IMAGE_EXTENSIONS:
                media_type = "image"
                source_url = raw_url or None
            elif ext.lower() in ("", "na", "unknown") and raw_url:
                # yt-dlp sets ext="NA" for image-only posts (e.g. Instagram photos)
                # Check the actual URL path for an image extension
                try:
                    path = urllib.parse.urlparse(raw_url).path
                    url_ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
                    if url_ext in self._IMAGE_EXTENSIONS:
                        media_type = "image"
                        source_url = raw_url
                except Exception:
                    pass

        return MediaInfo(
            id=info.get("id", ""),
            title=info.get("title", "Unknown"),
            description=info.get("description"),
            thumbnail=info.get("thumbnail"),
            duration=int(info["duration"]) if info.get("duration") is not None else None,
            uploader=info.get("uploader"),
            upload_date=info.get("upload_date"),
            view_count=info.get("view_count"),
            webpage_url=info.get("webpage_url", ""),
            extractor=info.get("extractor", ""),
            formats=formats,
            best_video_format=best_video,
            best_audio_format=best_audio,
            media_type=media_type,
            source_url=source_url,
        )

    def _get_quality_label(self, fmt: Dict[str, Any]) -> str:
        """Generate a human-readable quality label."""
        height = fmt.get("height")
        fps = fmt.get("fps")
        has_video = fmt.get("vcodec", "none") != "none"
        has_audio = fmt.get("acodec", "none") != "none"

        if not has_video and has_audio:
            abr = fmt.get("abr")
            if abr:
                return f"Audio {int(abr)}kbps"
            return "Audio Only"

        if height:
            label = f"{height}p"
            if fps and fps > 30:
                label += f"{int(fps)}"
            if not has_audio:
                label += " (video only)"
            return label

        return fmt.get("format_note", "Unknown")

    def download(
        self,
        url: str,
        output_path: Path,
        format_spec: Optional[str] = None,
        progress_hook: Optional[Callable] = None,
    ) -> str:
        """Download media from URL.

        Args:
            url: Media URL
            output_path: Directory to save file
            format_spec: yt-dlp format specification
            progress_hook: Callback for progress updates

        Returns:
            Path to downloaded file
        """
        opts = self._get_base_opts()
        opts.update({
            "outtmpl": str(output_path / "%(title)s.%(ext)s"),
            "format": format_spec or settings.default_format,
            "merge_output_format": "mp4",
            "writethumbnail": True,
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }],
            "quiet": False,
            "no_warnings": False,
        })

        if progress_hook:
            opts["progress_hooks"] = [progress_hook]

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise ValueError("Download failed")

                # Get the actual filename
                filename = ydl.prepare_filename(info)
                # Handle merged files
                if not Path(filename).exists():
                    # Try with .mp4 extension
                    mp4_file = Path(filename).with_suffix(".mp4")
                    if mp4_file.exists():
                        filename = str(mp4_file)

                return filename

            except yt_dlp.utils.DownloadError as e:
                logger.error(f"Download failed: {e}")
                raise ValueError(f"Download failed: {str(e)}")

    def download_audio(
        self,
        url: str,
        output_path: Path,
        progress_hook: Optional[Callable] = None,
    ) -> str:
        """Download audio only from URL."""
        opts = self._get_base_opts()
        opts.update({
            "outtmpl": str(output_path / "%(title)s.%(ext)s"),
            "format": "bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": False,
        })

        if progress_hook:
            opts["progress_hooks"] = [progress_hook]

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise ValueError("Download failed")

                filename = ydl.prepare_filename(info)
                # Audio extraction changes extension
                mp3_file = Path(filename).with_suffix(".mp3")
                if mp3_file.exists():
                    return str(mp3_file)

                return filename

            except yt_dlp.utils.DownloadError as e:
                logger.error(f"Audio download failed: {e}")
                raise ValueError(f"Audio download failed: {str(e)}")

    def download_instagram_video(
        self,
        url: str,
        output_path: Path,
        source_url: Optional[str] = None,
        title: Optional[str] = None,
        progress_hook: Optional[Callable] = None,
    ) -> str:
        """Download Instagram video directly via instaloader's session."""
        import instaloader

        L = self._get_instaloader()

        if progress_hook:
            progress_hook({"status": "downloading", "downloaded_bytes": 0, "total_bytes": 0})

        video_url = source_url
        if not video_url:
            shortcode = self._extract_instagram_shortcode(url)
            if not shortcode:
                raise ValueError("Could not extract Instagram shortcode from URL")
            try:
                post = instaloader.Post.from_shortcode(L.context, shortcode)
                if not post.is_video:
                    raise ValueError("Instagram post is not a video")
                video_url = post.video_url
                if not title:
                    title = (post.caption.split('\n')[0][:100]) if post.caption else shortcode
            except ValueError:
                raise
            except Exception as e:
                raise ValueError(f"Failed to get Instagram video info: {e}")

        if not video_url:
            raise ValueError("Could not determine video URL")

        # Sanitize filename
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title or 'instagram_video').strip().rstrip('.')
        if not safe_title:
            safe_title = "instagram_video"
        if len(safe_title) > 200:
            safe_title = safe_title[:200]

        filepath = output_path / f"{safe_title}.mp4"
        counter = 1
        while filepath.exists():
            filepath = output_path / f"{safe_title}_{counter}.mp4"
            counter += 1

        try:
            resp = L.context._session.get(video_url, stream=True)
            resp.raise_for_status()

            total = int(resp.headers.get('content-length', 0))
            downloaded = 0

            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_hook and total > 0:
                        progress_hook({
                            "status": "downloading",
                            "downloaded_bytes": downloaded,
                            "total_bytes": total,
                        })

        except Exception as e:
            if filepath.exists():
                filepath.unlink()
            logger.error(f"Instagram video download failed: {e}")
            raise ValueError(f"Instagram video download failed: {e}")

        if progress_hook:
            progress_hook({"status": "finished", "filename": str(filepath)})

        logger.info(f"Instagram video downloaded: {filepath}")
        return str(filepath)

    def download_image(
        self,
        url: str,
        output_path: Path,
        source_url: Optional[str] = None,
        title: Optional[str] = None,
        progress_hook: Optional[Callable] = None,
    ) -> str:
        """Download an image post (bypasses FFmpeg)."""
        if re.search(r'instagram\.com', url):
            return self._download_instagram_image(
                url, output_path, source_url, title, progress_hook,
            )
        return self._download_image_direct(
            url, output_path, source_url, title, progress_hook,
        )

    def _download_instagram_image(
        self,
        url: str,
        output_path: Path,
        source_url: Optional[str] = None,
        title: Optional[str] = None,
        progress_hook: Optional[Callable] = None,
    ) -> str:
        """Download Instagram image using instaloader."""
        import instaloader

        L = self._get_instaloader()

        if progress_hook:
            progress_hook({"status": "downloading", "downloaded_bytes": 0, "total_bytes": 0})

        # Get the direct image URL
        image_url = source_url
        if not image_url:
            shortcode = self._extract_instagram_shortcode(url)
            if not shortcode:
                raise ValueError("Could not extract Instagram shortcode from URL")
            try:
                post = instaloader.Post.from_shortcode(L.context, shortcode)
                image_url = post.url
                if not title:
                    title = (post.caption.split('\n')[0][:100]) if post.caption else shortcode
            except Exception as e:
                raise ValueError(f"Failed to get Instagram post info: {e}")

        if not image_url:
            raise ValueError("Could not determine image URL")

        # Determine extension from the CDN URL
        try:
            path = urllib.parse.urlparse(image_url).path
            url_ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        except Exception:
            url_ext = ""
        ext = url_ext if url_ext in self._IMAGE_EXTENSIONS else "jpg"

        # Sanitize filename
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title or 'instagram_image').strip().rstrip('.')
        if not safe_title:
            safe_title = "instagram_image"
        if len(safe_title) > 200:
            safe_title = safe_title[:200]

        filepath = output_path / f"{safe_title}.{ext}"
        counter = 1
        while filepath.exists():
            filepath = output_path / f"{safe_title}_{counter}.{ext}"
            counter += 1

        # Download using instaloader's authenticated session
        try:
            resp = L.context._session.get(image_url)
            resp.raise_for_status()
            filepath.write_bytes(resp.content)
        except Exception as e:
            logger.error(f"Instagram image download failed: {e}")
            raise ValueError(f"Instagram image download failed: {e}")

        if progress_hook:
            progress_hook({"status": "finished", "filename": str(filepath)})

        logger.info(f"Instagram image downloaded: {filepath}")
        return str(filepath)

    def _download_image_direct(
        self,
        url: str,
        output_path: Path,
        source_url: Optional[str] = None,
        title: Optional[str] = None,
        progress_hook: Optional[Callable] = None,
    ) -> str:
        """Download a non-Instagram image directly via HTTP."""
        # If no direct source_url, extract it from yt-dlp
        if not source_url:
            opts = self._get_base_opts()
            opts["extract_flat"] = False
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise ValueError("Could not extract info from URL")
                source_url = info.get("url")
                if not title:
                    title = info.get("title", "image")
            if not source_url:
                raise ValueError("Could not find direct image URL")

        if not title:
            title = "image"

        # Determine extension from the source URL path
        try:
            path = urllib.parse.urlparse(source_url).path
            url_ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        except Exception:
            url_ext = ""
        ext = url_ext if url_ext in self._IMAGE_EXTENSIONS else "jpg"

        # Sanitize filename
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title).strip().rstrip('.')
        if not safe_title:
            safe_title = "image"

        filepath = output_path / f"{safe_title}.{ext}"
        counter = 1
        while filepath.exists():
            filepath = output_path / f"{safe_title}_{counter}.{ext}"
            counter += 1

        # Download with browser UA and cookies
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

        cookie_jar = http.cookiejar.MozillaCookieJar()
        if self.cookies_file.exists():
            try:
                cookie_jar.load(str(self.cookies_file), ignore_discard=True, ignore_expires=True)
            except Exception as e:
                logger.warning(f"Could not load cookies for image download: {e}")

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )
        req = urllib.request.Request(source_url, headers=headers)

        try:
            if progress_hook:
                progress_hook({"status": "downloading", "downloaded_bytes": 0, "total_bytes": 0})

            with opener.open(req, timeout=60) as resp:
                data = resp.read()

            filepath.write_bytes(data)

            if progress_hook:
                progress_hook({"status": "finished", "filename": str(filepath)})

            logger.info(f"Image downloaded: {filepath}")
            return str(filepath)

        except Exception as e:
            logger.error(f"Image download failed: {e}")
            raise ValueError(f"Image download failed: {str(e)}")

    @staticmethod
    def get_format_for_quality(quality: str) -> str:
        """Get yt-dlp format string for quality preset."""
        quality_formats = {
            "best": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "4k": "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best",
            "1440p": "bestvideo[height<=1440][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1440]+bestaudio/best",
            "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best",
            "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best",
            "480p": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best",
            "360p": "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio/best",
            "audio_only": "bestaudio/best",
        }
        return quality_formats.get(quality, quality_formats["best"])


# Singleton instance
ytdlp_wrapper = YTDLPWrapper()
