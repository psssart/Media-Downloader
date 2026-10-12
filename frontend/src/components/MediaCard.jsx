import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Music, Video, Image, Clock, User, Eye, Loader2, Play, ExternalLink, CheckSquare, Square } from 'lucide-react';
import api from '../services/api';
import CompressionPanel, {
  COMPRESSION_OFF, buildCompressionOptions, useCompressionConfig,
} from './CompressionPanel';

const QUALITY_OPTIONS = [
  { value: 'best', label: 'Best Available' },
  { value: '4k', label: '4K (2160p)' },
  { value: '1440p', label: '1440p' },
  { value: '1080p', label: '1080p' },
  { value: '720p', label: '720p' },
  { value: '480p', label: '480p' },
  { value: '360p', label: '360p' },
];

function formatDuration(seconds) {
  if (!seconds) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatViews(count) {
  if (!count) return null;
  if (count >= 1000000) return `${(count / 1000000).toFixed(1)}M views`;
  if (count >= 1000) return `${(count / 1000).toFixed(1)}K views`;
  return `${count} views`;
}

function formatFileSize(bytes) {
  if (!bytes || bytes <= 0) return null;
  if (bytes >= 1073741824) return `~${(bytes / 1073741824).toFixed(1)} GB`;
  if (bytes >= 1048576) return `~${(bytes / 1048576).toFixed(0)} MB`;
  if (bytes >= 1024) return `~${(bytes / 1024).toFixed(0)} KB`;
  return `~${bytes} B`;
}

const SIZE_DEBOUNCE_MS = 3000;
const SIZE_WARN_BYTES = 50 * 1048576;
const SIZE_LARGE_BYTES = 300 * 1048576;

function sizeBadgeClass(bytes) {
  if (bytes > SIZE_LARGE_BYTES) return 'text-rose-200 bg-rose-900/60 border-rose-800/70';
  if (bytes > SIZE_WARN_BYTES) return 'text-amber-200 bg-amber-500/15 border-amber-500/30';
  return 'text-slate-500 border-transparent';
}

// Estimated size that settles SIZE_DEBOUNCE_MS after the last quality change; `pending` until then
function useDebouncedSize(bytes, key) {
  const [settled, setSettled] = useState(bytes);
  const [pending, setPending] = useState(false);
  const latest = useRef(bytes);
  latest.current = bytes;
  const settledKey = useRef(key);

  useEffect(() => {
    if (settledKey.current === key) {
      // Switched back to the already shown choice before the debounce fired
      setPending(false);
      return undefined;
    }
    setPending(true);
    const timer = setTimeout(() => {
      settledKey.current = key;
      setSettled(latest.current);
      setPending(false);
    }, SIZE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [key]);

  return { bytes: settled, pending };
}

function getFormatHeight(format) {
  if (!format.resolution) return null;
  // resolution is "WxH" or "Hp"
  const match = format.resolution.match(/(\d+)p/) || format.resolution.match(/\d+x(\d+)/);
  return match ? parseInt(match[1], 10) : null;
}

// Largest short side among the video formats (vertical videos count by width)
function getSourceShortSide(formats) {
  let best = 0;
  (formats || []).forEach((f) => {
    if (!f.has_video || !f.resolution) return;
    const wh = f.resolution.match(/^(\d+)x(\d+)$/);
    const short = wh ? Math.min(+wh[1], +wh[2]) : parseInt(f.resolution, 10);
    if (short > best) best = short;
  });
  return best || null;
}

function getRealSize(format) {
  return format.filesize ?? format.filesize_approx ?? null;
}

// Exact/approx size from the extractor, else bitrate (kbit/s) x duration
function getFormatSize(format, duration) {
  const size = getRealSize(format);
  if (size) return size;
  return format.tbr && duration ? Math.round(format.tbr * 125 * duration) : null;
}

// yt-dlp's default codec preference among equal resolutions
function codecRank(vcodec = '') {
  if (vcodec.startsWith('av01')) return 4;
  if (vcodec.startsWith('vp9') || vcodec.startsWith('vp09')) return 3;
  if (vcodec.startsWith('hev') || vcodec.startsWith('hvc') || vcodec.startsWith('h265')) return 2;
  return 1;
}

function pickBest(formats, rank) {
  const better = (x, y) => {
    const rx = rank(x), ry = rank(y);
    for (let i = 0; i < rx.length; i++) if (rx[i] !== ry[i]) return rx[i] > ry[i];
    return false;
  };
  return formats.reduce((best, f) => (best && !better(f, best) ? best : f), null);
}

// Mirrors the backend selectors: bestvideo[height<=N][ext=mp4] + bestaudio[ext=m4a].
// HLS variants of the same height often carry no size, so formats with a known size win ties.
function estimateFileSize(formats, quality, audioOnly, duration) {
  if (!formats || formats.length === 0) return null;

  const audioFormats = formats.filter(f => f.has_audio && !f.has_video);
  const bestAudio = pickBest(audioFormats, (f) => [
    !audioOnly && f.ext === 'm4a' ? 1 : 0, f.abr || 0, getRealSize(f) ? 1 : 0,
  ]);
  const audioSize = bestAudio ? getFormatSize(bestAudio, duration) : null;

  if (audioOnly) return audioSize;

  const maxHeight = quality === 'best' ? Infinity : quality === '4k' ? 2160 : parseInt(quality, 10);
  if (!maxHeight) return null;

  const eligible = formats.filter(f => {
    if (!f.has_video) return false;
    const h = getFormatHeight(f);
    return maxHeight === Infinity || (h && h <= maxHeight);
  });
  const mp4 = eligible.filter(f => f.ext === 'mp4');
  const videoFormat = pickBest(mp4.length > 0 ? mp4 : eligible, (f) => [
    getFormatHeight(f) || 0, getRealSize(f) ? 1 : 0, codecRank(f.vcodec), f.fps || 0, f.tbr || 0,
  ]);
  if (!videoFormat) return null;

  const videoSize = getFormatSize(videoFormat, duration);
  if (!videoSize) return null;

  // If the video format already includes audio, don't add audio size
  if (videoFormat.has_audio) return videoSize;
  return audioSize ? videoSize + audioSize : videoSize;
}

function getEmbedUrl(media) {
  const extractor = (media.extractor || '').toLowerCase();

  if (extractor.includes('youtube')) {
    return `https://www.youtube.com/embed/${media.id}?autoplay=1&rel=0`;
  }
  if (extractor.includes('vimeo')) {
    return `https://player.vimeo.com/video/${media.id}?autoplay=1`;
  }
  if (extractor.includes('dailymotion')) {
    return `https://www.dailymotion.com/embed/video/${media.id}?autoplay=1`;
  }

  // No embed available for other sites (Instagram, Facebook, TikTok, etc.)
  return null;
}

// Carousel/gallery view for multi-entry posts
function GalleryView({ media, onDownload, downloading }) {
  const entries = media.entries || [];
  const [selected, setSelected] = useState(() => new Set(entries.map((_, i) => i)));
  const [quality, setQuality] = useState('best');

  const hasVideos = entries.some(e => e.media_type !== 'image');
  const allSelected = selected.size === entries.length;

  const toggleAll = () => {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(entries.map((_, i) => i)));
    }
  };

  const toggleEntry = (index) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const handleDownloadSelected = () => {
    for (const index of selected) {
      const entry = entries[index];
      onDownload({
        url: entry.webpage_url,
        quality: entry.media_type === 'image' ? 'best' : quality,
        audioOnly: false,
        mediaType: entry.media_type,
        sourceUrl: entry.source_url,
        title: entry.title,
      });
    }
  };

  return (
    <div className="bg-slate-800 rounded-xl overflow-hidden border border-slate-700 shadow-xl">
      {/* Header info */}
      <div className="p-6 pb-4">
        <h2 className="text-xl font-semibold mb-2 line-clamp-2">{media.title}</h2>
        <div className="flex flex-wrap gap-4 text-sm text-slate-400 mb-2">
          {media.uploader && (
            <span className="flex items-center gap-1">
              <User className="w-4 h-4" />
              {media.uploader}
            </span>
          )}
          <span className="flex items-center gap-1 text-primary-400">
            {media.extractor}
          </span>
          <span className="px-2 py-0.5 bg-purple-900/50 text-purple-400 rounded text-xs">
            {entries.length} items
          </span>
        </div>
        {media.description && (
          <p className="text-slate-400 text-sm line-clamp-2">{media.description}</p>
        )}
      </div>

      {/* Gallery grid */}
      <div className="px-6 pb-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3 max-h-80 overflow-y-auto pr-1">
          {entries.map((entry, index) => (
            <button
              key={entry.id || index}
              onClick={() => toggleEntry(index)}
              className={`relative aspect-square rounded-lg overflow-hidden border-2 transition-all cursor-pointer
                ${selected.has(index)
                  ? 'border-primary-500 ring-2 ring-primary-500/30'
                  : 'border-slate-600 hover:border-slate-500'
                }`}
            >
              {entry.thumbnail ? (
                <img
                  src={api.getProxyThumbnailUrl(entry.thumbnail)}
                  alt={entry.title}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="w-full h-full bg-slate-700 flex items-center justify-center">
                  {entry.media_type === 'image' ? (
                    <Image className="w-8 h-8 text-slate-500" />
                  ) : (
                    <Video className="w-8 h-8 text-slate-500" />
                  )}
                </div>
              )}

              {/* Checkbox overlay */}
              <div className="absolute top-2 left-2">
                {selected.has(index) ? (
                  <CheckSquare className="w-5 h-5 text-primary-400 drop-shadow-lg" />
                ) : (
                  <Square className="w-5 h-5 text-white/70 drop-shadow-lg" />
                )}
              </div>

              {/* Media type indicator */}
              <div className="absolute bottom-1 right-1">
                {entry.media_type === 'image' ? (
                  <span className="px-1.5 py-0.5 bg-black/70 rounded text-xs text-emerald-400">
                    Photo
                  </span>
                ) : entry.duration ? (
                  <span className="px-1.5 py-0.5 bg-black/70 rounded text-xs">
                    {formatDuration(entry.duration)}
                  </span>
                ) : (
                  <span className="px-1.5 py-0.5 bg-black/70 rounded text-xs text-blue-400">
                    Video
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Controls */}
      <div className="px-6 pb-6 flex flex-wrap items-center gap-4 border-t border-slate-700 pt-4">
        <button
          onClick={toggleAll}
          className="text-sm text-slate-400 hover:text-white transition-colors"
        >
          {allSelected ? 'Deselect All' : 'Select All'}
        </button>

        {hasVideos && (
          <select
            value={quality}
            onChange={(e) => setQuality(e.target.value)}
            disabled={downloading}
            className="px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg text-sm
                       focus:outline-none focus:ring-2 focus:ring-primary-500
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {QUALITY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        )}

        <div className="ml-auto">
          <button
            onClick={handleDownloadSelected}
            disabled={downloading || selected.size === 0}
            className="px-6 py-2 bg-primary-600 hover:bg-primary-700
                       disabled:bg-slate-700 disabled:cursor-not-allowed
                       rounded-lg font-medium transition-colors flex items-center gap-2"
          >
            {downloading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Starting...
              </>
            ) : (
              <>
                <Download className="w-4 h-4" />
                Download Selected ({selected.size})
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

// Single image view
function ImageCard({ media, onDownload, downloading }) {
  const handleDownload = () => {
    onDownload({
      url: media.webpage_url,
      quality: 'best',
      audioOnly: false,
      mediaType: 'image',
      sourceUrl: media.source_url,
      title: media.title,
    });
  };

  return (
    <div className="bg-slate-800 rounded-xl overflow-hidden border border-slate-700 shadow-xl">
      <div className="md:flex items-center">
        {/* Image preview */}
        <div className="md:w-80 md:flex-shrink-0">
          <div className="relative aspect-square bg-black">
            {media.thumbnail ? (
              <img
                src={api.getProxyThumbnailUrl(media.thumbnail)}
                alt={media.title}
                className="w-full h-full object-cover"
              />
            ) : (
              <div className="w-full h-full bg-slate-700 flex items-center justify-center">
                <Image className="w-16 h-16 text-slate-600" />
              </div>
            )}
            <span className="absolute bottom-2 right-2 px-2 py-1 bg-black/80 rounded text-sm font-medium text-emerald-400">
              Photo
            </span>
          </div>
        </div>

        {/* Info */}
        <div className="p-6 flex-1 flex flex-col">
          <h2 className="text-xl font-semibold mb-2 line-clamp-2">{media.title}</h2>

          <div className="flex flex-wrap gap-4 text-sm text-slate-400 mb-4">
            {media.uploader && (
              <span className="flex items-center gap-1">
                <User className="w-4 h-4" />
                {media.uploader}
              </span>
            )}
            {media.view_count && (
              <span className="flex items-center gap-1">
                <Eye className="w-4 h-4" />
                {formatViews(media.view_count)}
              </span>
            )}
            <span className="flex items-center gap-1 text-primary-400">
              {media.extractor}
            </span>
          </div>

          {media.description && (
            <p className="text-slate-400 text-sm mb-4 line-clamp-2">
              {media.description}
            </p>
          )}

          {/* Download button only (no quality/audio controls for images) */}
          <div className="mt-auto flex justify-end">
            <button
              onClick={handleDownload}
              disabled={downloading}
              className="px-6 py-2 bg-primary-600 hover:bg-primary-700
                         disabled:bg-slate-700 disabled:cursor-not-allowed
                         rounded-lg font-medium transition-colors flex items-center gap-2"
            >
              {downloading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <Download className="w-4 h-4" />
                  Download Photo
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function MediaCard({ media, onDownload, downloading }) {
  // Carousel/playlist with multiple entries
  if (media.entries && media.entries.length > 0) {
    return <GalleryView media={media} onDownload={onDownload} downloading={downloading} />;
  }

  // Single image
  if (media.media_type === 'image') {
    return <ImageCard media={media} onDownload={onDownload} downloading={downloading} />;
  }

  // Single video (existing behavior)
  return <VideoCard media={media} onDownload={onDownload} downloading={downloading} />;
}

function VideoCard({ media, onDownload, downloading }) {
  const [quality, setQuality] = useState('best');
  const [audioOnly, setAudioOnly] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [compression, setCompression] = useState(COMPRESSION_OFF);
  const [rejection, setRejection] = useState(null);
  const [compressionStatus, setCompressionStatus] = useState('off');
  const [autoHeight, setAutoHeight] = useState(null);
  const compressionConfig = useCompressionConfig();
  const compressing = compression.enabled && !audioOnly;

  // With compression on, the selector picks the output resolution instead of the source quality
  const sourceShort = getSourceShortSide(media.formats);
  const resolutionOptions = (compressionConfig?.resolutions || [])
    .filter((h) => !sourceShort || h <= sourceShort);

  const handleCompressionStatus = useCallback((status, height) => {
    setCompressionStatus(status);
    setAutoHeight(height);
  }, []);

  const compressionOptions = audioOnly ? null : buildCompressionOptions(compression);
  // With compression on, downloading is only possible once the settings pass admission
  const blocked = !audioOnly && compression.enabled && compressionStatus !== 'ok';
  const blockedReason = compressionStatus === 'checking'
    ? 'Checking compression settings…'
    : 'File is too large or long, configure compression';

  const handleCompressionChange = (value) => {
    setCompression(value);
    setRejection(null);
  };

  const handleDownload = async () => {
    setRejection(null);
    const result = await onDownload({
      url: media.webpage_url,
      quality: audioOnly ? 'audio_only' : (compressionOptions ? 'best' : quality),
      audioOnly,
      mediaType: 'video',
      sourceUrl: media.source_url,
      title: media.title,
      compression: compressionOptions,
    });
    // Admission rejected: show the reason and alternatives next to the controls
    if (result?.rejection) setRejection(result.rejection);
  };

  const sizeEstimate = useDebouncedSize(
    estimateFileSize(media.formats, quality, audioOnly, media.duration),
    `${quality}|${audioOnly}`,
  );
  const estimatedSize = formatFileSize(sizeEstimate.bytes);

  return (
    <div className="bg-slate-800 rounded-xl overflow-hidden border border-slate-700 shadow-xl">
      <div className="md:flex items-center">
        {/* Thumbnail / Video Preview */}
        <div className="md:w-80 md:flex-shrink-0">
          <div className="relative aspect-video bg-black">
            {playing && getEmbedUrl(media) ? (
              <iframe
                src={getEmbedUrl(media)}
                className="absolute inset-0 w-full h-full"
                allow="autoplay; encrypted-media; picture-in-picture"
                allowFullScreen
                frameBorder="0"
              />
            ) : (
              <>
                {media.thumbnail ? (
                  <img
                    src={api.getProxyThumbnailUrl(media.thumbnail)}
                    alt={media.title}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full bg-slate-700 flex items-center justify-center">
                    <Video className="w-16 h-16 text-slate-600" />
                  </div>
                )}
                {/* Play/link button overlay */}
                {getEmbedUrl(media) ? (
                  <button
                    onClick={() => setPlaying(true)}
                    className="absolute inset-0 flex items-center justify-center group cursor-pointer"
                    aria-label="Play preview"
                  >
                    <div className="w-14 h-14 rounded-full bg-black/50 backdrop-blur-sm
                                    border-2 border-white/20 flex items-center justify-center
                                    group-hover:bg-primary-600/80 group-hover:border-primary-400/40
                                    group-hover:scale-110 transition-all duration-200 shadow-lg">
                      <Play className="w-6 h-6 text-white fill-white ml-0.5" />
                    </div>
                  </button>
                ) : (
                  <a
                    href={media.webpage_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="absolute inset-0 flex items-center justify-center group cursor-pointer"
                    aria-label="Open on source site"
                  >
                    <div className="w-14 h-14 rounded-full bg-black/50 backdrop-blur-sm
                                    border-2 border-white/20 flex items-center justify-center
                                    group-hover:bg-primary-600/80 group-hover:border-primary-400/40
                                    group-hover:scale-110 transition-all duration-200 shadow-lg">
                      <ExternalLink className="w-6 h-6 text-white" />
                    </div>
                  </a>
                )}
                {media.duration && (
                  <span className="absolute bottom-2 right-2 px-2 py-1 bg-black/80 rounded text-sm font-medium">
                    {formatDuration(media.duration)}
                  </span>
                )}
              </>
            )}
          </div>
        </div>

        {/* Info */}
        <div className="p-6 flex-1 flex flex-col">
          <h2 className="text-xl font-semibold mb-2 line-clamp-2">{media.title}</h2>

          <div className="flex flex-wrap gap-4 text-sm text-slate-400 mb-4">
            {media.uploader && (
              <span className="flex items-center gap-1">
                <User className="w-4 h-4" />
                {media.uploader}
              </span>
            )}
            {media.view_count && (
              <span className="flex items-center gap-1">
                <Eye className="w-4 h-4" />
                {formatViews(media.view_count)}
              </span>
            )}
            <span className="flex items-center gap-1 text-primary-400">
              {media.extractor}
            </span>
          </div>

          {media.description && (
            <p className="text-slate-400 text-sm mb-4 line-clamp-2">
              {media.description}
            </p>
          )}

          {/* Format info */}
          <div className="flex flex-wrap gap-2 mb-4 text-xs">
            {media.best_video_format && (
              <span className="px-2 py-1 bg-green-900/50 text-green-400 rounded">
                Best: {media.best_video_format.quality_label}
              </span>
            )}
            {media.best_audio_format && (
              <span className="px-2 py-1 bg-blue-900/50 text-blue-400 rounded">
                Audio: {media.best_audio_format.quality_label}
              </span>
            )}
          </div>

          {/* Download options */}
          <div className="mt-auto flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              {compressing ? (
                <select
                  value={compression.resolution || 'auto'}
                  onChange={(e) => handleCompressionChange({
                    ...compression,
                    resolution: e.target.value === 'auto' ? null : Number(e.target.value),
                    overrides: {},
                    trimMax: null,
                  })}
                  disabled={downloading}
                  aria-label="Output resolution"
                  title="Output resolution of the compressed file"
                  className="px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg
                             focus:outline-none focus:ring-2 focus:ring-primary-500
                             disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <option value="auto">
                    {autoHeight && !compression.resolution ? `Auto (${autoHeight}p)` : 'Auto (best that fits)'}
                  </option>
                  {resolutionOptions.map((h) => (
                    <option key={h} value={h}>{h}p</option>
                  ))}
                </select>
              ) : (
                <select
                  value={quality}
                  onChange={(e) => setQuality(e.target.value)}
                  disabled={audioOnly || downloading}
                  className="px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg
                             focus:outline-none focus:ring-2 focus:ring-primary-500
                             disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {QUALITY_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              )}
            </div>

            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={audioOnly}
                onChange={(e) => setAudioOnly(e.target.checked)}
                disabled={downloading}
                className="w-4 h-4 rounded border-slate-600 bg-slate-700
                           text-primary-600 focus:ring-primary-500"
              />
              <Music className="w-4 h-4" />
              <span className="text-sm">Audio Only</span>
            </label>

            <div className="ml-auto flex items-center gap-3">
              {!compressionOptions && (sizeEstimate.pending ? (
                <Loader2
                  className="w-4 h-4 animate-spin text-slate-500"
                  aria-label="Estimating file size"
                />
              ) : estimatedSize && (
                <span
                  className={`text-xs px-2 py-0.5 rounded-md border transition-colors ${sizeBadgeClass(sizeEstimate.bytes)}`}
                  title="Estimated file size"
                >
                  {estimatedSize}
                </span>
              ))}
              {/* Disabled buttons get no hover events in some browsers, so the tooltip lives on a wrapper */}
              <span className="relative group" tabIndex={blocked ? 0 : undefined}>
                <button
                  onClick={handleDownload}
                  disabled={downloading || blocked}
                  aria-describedby={blocked ? 'download-blocked-reason' : undefined}
                  className="px-6 py-2 bg-primary-600 hover:bg-primary-700
                             disabled:bg-slate-700 disabled:text-slate-400 disabled:cursor-not-allowed
                             rounded-lg font-medium transition-colors flex items-center gap-2"
                >
                  {downloading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Starting...
                    </>
                  ) : (
                    <>
                      <Download className="w-4 h-4" />
                      Download
                    </>
                  )}
                </button>
                {blocked && !downloading && (
                  <span
                    id="download-blocked-reason"
                    role="tooltip"
                    className="pointer-events-none absolute bottom-full right-0 mb-2 w-max max-w-[16rem]
                               px-2 py-1 text-xs text-slate-100 bg-slate-950 border border-slate-700
                               rounded shadow-lg opacity-0 transition-opacity
                               group-hover:opacity-100 group-focus-within:opacity-100 group-focus:opacity-100"
                  >
                    {blockedReason}
                  </span>
                )}
              </span>
            </div>

            <CompressionPanel
              url={media.webpage_url}
              duration={media.duration}
              value={compression}
              onChange={handleCompressionChange}
              onStatusChange={handleCompressionStatus}
              disabled={audioOnly || downloading}
              rejection={rejection}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
