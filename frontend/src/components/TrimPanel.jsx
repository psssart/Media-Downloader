import { Scissors, X } from 'lucide-react';
import TrimSlider from './TrimSlider';

export const TRIM_OFF = {
  enabled: false,
  start: 0,
  end: null, // null = end of the video
  maxLength: null, // longest clip compression admitted ("Trim to X" alternative); only applies while compressing
};

// Trim state -> {trim_start, trim_end} for the API, or null when the whole video is kept
export function buildTrimRange(trim, duration) {
  if (!trim.enabled || !(duration > 0)) return null;
  const start = trim.start > 0 ? trim.start : null;
  const end = trim.end != null && trim.end < duration ? trim.end : null;
  return start == null && end == null ? null : { trim_start: start, trim_end: end };
}

// Range carried by compression options (alternatives) -> trim state with that clip length as the cap
export function trimFromOptions(options, duration) {
  if (options.trim_start == null && options.trim_end == null) return null;
  const start = options.trim_start || 0;
  const end = Math.min(options.trim_end ?? duration, duration);
  return { enabled: true, start, end, maxLength: end - start };
}

export default function TrimPanel({
  duration, value, onChange, compressing, audioOnly, disabled,
}) {
  if (!(duration > 1)) return null;

  const end = value.end ?? duration;
  const capped = compressing && value.maxLength != null;
  const maxLength = capped ? value.maxLength : duration;

  return (
    <div className="w-full space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={value.enabled}
            onChange={(e) => onChange({ ...value, enabled: e.target.checked, maxLength: null })}
            disabled={disabled}
            className="w-4 h-4 rounded border-slate-600 bg-slate-700
                       text-primary-600 focus:ring-primary-500"
          />
          <Scissors className="w-4 h-4" />
          <span className="text-sm">Trim</span>
        </label>

        {value.enabled && capped && (
          <span className="flex items-center gap-1 px-2 py-1 text-xs bg-slate-700 rounded">
            limited for compression
            <button
              onClick={() => onChange({ ...value, maxLength: null })}
              disabled={disabled}
              className="text-slate-400 hover:text-white"
              title="Remove the length limit"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        )}
      </div>

      {value.enabled && (
        <div className="text-xs max-w-md space-y-1">
          <TrimSlider
            duration={duration}
            maxLength={maxLength}
            start={value.start}
            end={end}
            disabled={disabled}
            onChange={(start, newEnd) => onChange({ ...value, start, end: newEnd })}
          />
          {!compressing && !audioOnly && (
            <p className="text-slate-500">
              Cut without re-encoding: the clip starts at the nearest keyframe, up to a few seconds early.
              Compress cuts exactly.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
