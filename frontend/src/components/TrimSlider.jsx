// Dual-thumb range for picking a clip of at most `maxLength` seconds (pass the duration for no limit).
// Widening past the limit drags the other thumb along; pushing a thumb into
// the other one slides the whole clip, keeping its length.

import { useState } from 'react';

const MIN_LENGTH = 1;

export function formatClock(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = (s % 60).toString().padStart(2, '0');
  return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

// "12345" -> "1:23:45": colons are inserted every two digits from the right
function digitsToClock(digits) {
  const parts = [];
  for (let i = digits.length; i > 0; i -= 2) parts.unshift(digits.slice(Math.max(0, i - 2), i));
  return parts.join(':');
}

function digitsToSeconds(digits) {
  const n = digits.padStart(6, '0');
  return Number(n.slice(0, -4)) * 3600 + Number(n.slice(-4, -2)) * 60 + Number(n.slice(-2));
}

// Time field that takes digits only; Enter/blur commits, Escape reverts
function TimeInput({ value, onCommit, maxDigits, disabled, label }) {
  const [draft, setDraft] = useState(null);
  const shown = draft === null ? formatClock(value) : digitsToClock(draft);

  const commit = (e) => {
    const cancelled = e.target.dataset.cancel;
    delete e.target.dataset.cancel;
    if (draft && !cancelled) onCommit(digitsToSeconds(draft));
    setDraft(null);
  };

  return (
    <input
      type="text"
      inputMode="numeric"
      aria-label={label}
      disabled={disabled}
      value={shown}
      size={Math.max(4, shown.length)}
      onFocus={(e) => {
        setDraft(formatClock(value).replace(/\D/g, ''));
        e.target.select();
      }}
      onChange={(e) => setDraft(e.target.value.replace(/\D/g, '').replace(/^0+(?=\d{3})/, '').slice(-maxDigits))}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.target.blur();
        if (e.key === 'Escape') {
          // Tell onBlur to drop the draft instead of committing it
          e.target.dataset.cancel = '1';
          e.target.blur();
        }
      }}
      className="w-auto bg-transparent text-center tabular-nums text-slate-200 px-1 py-0.5 rounded
        border border-transparent hover:border-slate-600 focus:border-primary-500 focus:bg-slate-800
        focus:outline-none transition-colors disabled:opacity-50"
    />
  );
}

export default function TrimSlider({ duration, maxLength, start, end, onChange, disabled }) {
  const limit = Math.min(maxLength, duration);
  const pct = (v) => `${(v / duration) * 100}%`;

  const length = end - start;
  const maxDigits = duration >= 3600 ? 6 : 4;

  const moveStart = (value) => {
    let s = Math.min(Math.max(0, value), duration - MIN_LENGTH);
    let e = end;
    if (s > e - MIN_LENGTH) {
      e = Math.min(duration, s + length);
      s = Math.min(s, e - MIN_LENGTH);
    } else if (e - s > limit) {
      e = s + limit;
    }
    onChange(s, e);
  };

  const moveEnd = (value) => {
    let e = Math.max(Math.min(duration, value), MIN_LENGTH);
    let s = start;
    if (e < s + MIN_LENGTH) {
      s = Math.max(0, e - length);
      e = Math.max(e, s + MIN_LENGTH);
    } else if (e - s > limit) {
      s = e - limit;
    }
    onChange(s, e);
  };

  return (
    <div className="space-y-1">
      <div className="relative h-5">
        <div className="absolute top-1/2 -translate-y-1/2 w-full h-1.5 bg-slate-700 rounded-full" />
        <div
          className="absolute top-1/2 -translate-y-1/2 h-1.5 bg-primary-500 rounded-full"
          style={{ left: pct(start), width: pct(end - start) }}
        />
        <input
          type="range"
          min={0}
          max={duration}
          step={1}
          value={start}
          onChange={(e) => moveStart(Number(e.target.value))}
          disabled={disabled}
          aria-label="Clip start"
          className="range-dual"
          // When the thumbs meet near the end, the start thumb must stay grabbable
          style={{ zIndex: start > duration / 2 ? 3 : 1 }}
        />
        <input
          type="range"
          min={0}
          max={duration}
          step={1}
          value={end}
          onChange={(e) => moveEnd(Number(e.target.value))}
          disabled={disabled}
          aria-label="Clip end"
          className="range-dual"
          style={{ zIndex: 2 }}
        />
      </div>
      <div className="flex justify-between items-center text-slate-400">
        <span className="flex items-center gap-0.5">
          <TimeInput value={start} onCommit={moveStart} maxDigits={maxDigits} disabled={disabled} label="Clip start time" />
          –
          <TimeInput value={end} onCommit={moveEnd} maxDigits={maxDigits} disabled={disabled} label="Clip end time" />
        </span>
        <span>
          {formatClock(end - start)}
          {limit < duration ? ` of max ${formatClock(limit)}` : ` of ${formatClock(duration)}`}
        </span>
      </div>
    </div>
  );
}
