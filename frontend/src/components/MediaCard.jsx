import { useState } from 'react';
import { Download, Music, Video, Clock, User, Eye, Loader2, Play, ExternalLink } from 'lucide-react';
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

export default function MediaCard({ media, onDownload, downloading }) {
  const [quality, setQuality] = useState('best');
  const [audioOnly, setAudioOnly] = useState(false);
  const [playing, setPlaying] = useState(false);

  const handleDownload = () => {
    onDownload({
      url: media.webpage_url,
      quality: audioOnly ? 'audio_only' : quality,
      audioOnly,
    });
  };

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
              <span className="text-sm">Audio Only (MP3)</span>
            </label>

            <button
              onClick={handleDownload}
              disabled={downloading}
              className="ml-auto px-6 py-2 bg-primary-600 hover:bg-primary-700
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
  );
}
