import yt_dlp
from typing import Optional, Dict, Any, Callable
from pathlib import Path
import logging

from ..config import settings
from ..models import MediaInfo, FormatInfo

logger = logging.getLogger(__name__)


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

        return opts

    def extract_info(self, url: str) -> MediaInfo:
        """Extract media information from URL."""
        opts = self._get_base_opts()
        opts["extract_flat"] = False

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)

                if info is None:
                    raise ValueError("Could not extract info from URL")

                return self._parse_info(info)

            except yt_dlp.utils.DownloadError as e:
                logger.error(f"Failed to extract info: {e}")
                raise ValueError(f"Failed to extract info: {str(e)}")

    def _parse_info(self, info: Dict[str, Any]) -> MediaInfo:
        """Parse yt-dlp info dict into MediaInfo model."""
        formats = []
        best_video = None
        best_audio = None
        best_video_height = 0
        best_audio_bitrate = 0

        for fmt in info.get("formats", []):
            has_video = fmt.get("vcodec", "none") != "none"
            has_audio = fmt.get("acodec", "none") != "none"

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

        return MediaInfo(
            id=info.get("id", ""),
            title=info.get("title", "Unknown"),
            description=info.get("description"),
            thumbnail=info.get("thumbnail"),
            duration=info.get("duration"),
            uploader=info.get("uploader"),
            upload_date=info.get("upload_date"),
            view_count=info.get("view_count"),
            webpage_url=info.get("webpage_url", ""),
            extractor=info.get("extractor", ""),
            formats=formats,
            best_video_format=best_video,
            best_audio_format=best_audio,
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
