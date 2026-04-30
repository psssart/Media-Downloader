import { useState } from 'react';
import { Download, Music, Video, Image, Clock, User, Eye, Loader2, Play, ExternalLink, CheckSquare, Square } from 'lucide-react';
import api from '../services/api';

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

function getFormatHeight(format) {
  if (!format.resolution) return null;
  // resolution is "WxH" or "Hp"
  const match = format.resolution.match(/(\d+)p/) || format.resolution.match(/\d+x(\d+)/);
  return match ? parseInt(match[1], 10) : null;
}

function getFormatSize(format) {
  return format.filesize ?? format.filesize_approx ?? null;
}

function estimateFileSize(formats, quality, audioOnly) {
  if (!formats || formats.length === 0) return null;

  const audioFormats = formats.filter(f => f.has_audio && !f.has_video);
  const videoFormats = formats.filter(f => f.has_video);

  // Best audio-only format (highest abr)
  const bestAudio = audioFormats.length > 0
    ? audioFormats.reduce((best, f) => (f.abr || 0) > (best.abr || 0) ? f : best)
    : null;

  if (audioOnly) {
    if (!bestAudio) return null;
    return getFormatSize(bestAudio);
  }

  // Find the appropriate video format
  let videoFormat = null;

  if (quality === 'best') {
    // Largest video format by size, falling back to highest resolution
    const withSize = videoFormats.filter(f => getFormatSize(f));
    if (withSize.length > 0) {
      videoFormat = withSize.reduce((best, f) => getFormatSize(f) > getFormatSize(best) ? f : best);
    } else if (videoFormats.length > 0) {
      videoFormat = videoFormats.reduce((best, f) => (getFormatHeight(f) || 0) > (getFormatHeight(best) || 0) ? f : best);
    }
  } else {
    // Quality like "1080p", "720p", "4k" etc.
    const maxHeight = quality === '4k' ? 2160 : parseInt(quality, 10);
    if (!maxHeight) return null;

    // Filter to video-only formats at or below the target height, pick the best one
    const eligible = videoFormats.filter(f => {
      const h = getFormatHeight(f);
      return h && h <= maxHeight;
    });

    if (eligible.length > 0) {
      videoFormat = eligible.reduce((best, f) => (getFormatHeight(f) || 0) > (getFormatHeight(best) || 0) ? f : best);
    }
  }

  if (!videoFormat) return null;

  const videoSize = getFormatSize(videoFormat);
  if (!videoSize) return null;

  // If the video format already includes audio, don't add audio size
  if (videoFormat.has_audio) return videoSize;

  const audioSize = bestAudio ? getFormatSize(bestAudio) : null;
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

  const handleDownload = () => {
    onDownload({
      url: media.webpage_url,
      quality: audioOnly ? 'audio_only' : quality,
      audioOnly,
      mediaType: 'video',
    });
  };

  const estimatedSize = formatFileSize(estimateFileSize(media.formats, quality, audioOnly));

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
              {estimatedSize && (
                <span className="text-xs text-slate-500">{estimatedSize}</span>
              )}
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
                    Download
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
