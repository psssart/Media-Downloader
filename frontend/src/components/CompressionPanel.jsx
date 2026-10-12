import { useEffect, useState } from 'react';
import { Shrink, Loader2, AlertTriangle, CheckCircle2, X } from 'lucide-react';
import api from '../services/api';
import TrimSlider, { formatClock } from './TrimSlider';

export const COMPRESSION_OFF = {
  enabled: false,
  presetId: null,
  customMb: '',
  overrides: {},
  resolution: null, // exact output short side; null = auto (best that fits)
  trimMax: null, // longest clip that passed admission (UI only, not sent)
};

// Overrides that come from admission alternatives (not set directly by the user)
const OVERRIDE_KEYS = ['max_height', 'prefer_h264', 'encoder_preset', 'trim_start', 'trim_end', 'deferred'];

let presetsPromise = null;
export function loadPresets() {
  if (!presetsPromise) {
    presetsPromise = api.getCompressionPresets().catch((err) => {
      presetsPromise = null;
      throw err;
    });
  }
  return presetsPromise;
}

function formatSeconds(seconds) {
  if (seconds == null) return null;
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  if (m >= 60) return `${Math.floor(m / 60)}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

// Panel state -> CompressionOptions for the API (null when off/incomplete)
export function buildCompressionOptions(value) {
  if (!value.enabled) return null;
  const options = { ...value.overrides };
  if (value.resolution) options.resolution = value.resolution;
  if (value.presetId && value.presetId !== 'custom') {
    options.preset_id = value.presetId;
  } else {
    const mb = parseFloat(value.customMb);
    if (!(mb > 0)) return null;
    options.target_size_mb = mb;
  }
  return options;
}

// Alternative's full options -> panel state
function valueFromOptions(current, options) {
  const overrides = {};
  OVERRIDE_KEYS.forEach((key) => {
    if (options[key] != null && options[key] !== false) overrides[key] = options[key];
  });
  return {
    ...current,
    presetId: options.preset_id || 'custom',
    customMb: options.preset_id ? current.customMb : String(options.target_size_mb ?? current.customMb),
    overrides,
    resolution: 'resolution' in options ? options.resolution ?? null : current.resolution,
    trimMax: options.trim_end != null ? options.trim_end - (options.trim_start || 0) : null,
  };
}

// Panel state -> what the Download button may do
function compressionStatus(value, options, estimate, checking, rejection) {
  if (!value.enabled) return 'off';
  if (!options) return 'incomplete';
  if (rejection) return 'rejected';
  if (checking || !estimate) return 'checking';
  return estimate.admitted ? 'ok' : 'rejected';
}

function describeOverrides(overrides) {
  const parts = [];
  if (overrides.max_height) parts.push(`${overrides.max_height}p`);
  if (overrides.prefer_h264) parts.push('H.264 source');
  if (overrides.encoder_preset) parts.push(`${overrides.encoder_preset} preset`);
  if (overrides.trim_end != null) {
    parts.push(`clip ${formatClock(overrides.trim_start || 0)}–${formatClock(overrides.trim_end)}`);
  }
  if (overrides.deferred) parts.push('deferred');
  return parts.join(' · ');
}

export function useCompressionConfig() {
  const [config, setConfig] = useState(null);
  useEffect(() => {
    loadPresets()
      .then(setConfig)
      .catch(() => setConfig({ enabled: false, presets: [], resolutions: [] }));
  }, []);
  return config;
}

export default function CompressionPanel({
  url, duration, value, onChange, onStatusChange, disabled, rejection,
}) {
  const config = useCompressionConfig();
  const [estimate, setEstimate] = useState(null);
  const [checking, setChecking] = useState(false);

  const options = buildCompressionOptions(value);
  const optionsKey = JSON.stringify(options);

  // Dry-run admission whenever the request changes (debounced)
  useEffect(() => {
    setEstimate(null);
    if (!value.enabled || !options || disabled) return undefined;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setChecking(true);
      try {
        // The output resolution is part of the compression options, so the source quality stays 'best'
        setEstimate(await api.estimateCompression(url, 'best', options, { signal: controller.signal }));
      } catch (err) {
        if (err.name !== 'AbortError') setEstimate({ admitted: false, message: err.message, alternatives: [] });
      } finally {
        if (!controller.signal.aborted) setChecking(false);
      }
    }, 600);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, optionsKey, value.enabled, disabled]);

  const status = compressionStatus(value, options, estimate, checking, rejection);
  // Resolution "auto" resolved to, for the resolution selector label
  const autoHeight = status === 'ok' && !value.resolution && estimate
    ? Math.min(estimate.width, estimate.height) : null;
  useEffect(() => {
    onStatusChange?.(status, autoHeight);
  }, [status, autoHeight, onStatusChange]);

  if (!config || !config.enabled) return null;

  const toggle = (enabled) => {
    onChange({
      ...value,
      enabled,
      presetId: value.presetId || config.presets[0]?.id || 'custom',
    });
  };

  // A rejection from /start (state changed since the estimate) takes precedence
  const verdict = rejection || estimate;
  const hasOverrides = Object.keys(value.overrides).length > 0;

  return (
    <div className="w-full space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={value.enabled}
            onChange={(e) => toggle(e.target.checked)}
            disabled={disabled}
            className="w-4 h-4 rounded border-slate-600 bg-slate-700
                       text-primary-600 focus:ring-primary-500"
          />
          <Shrink className="w-4 h-4" />
          <span className="text-sm">Compress</span>
        </label>

        {value.enabled && (
          <>
            <select
              value={value.presetId || 'custom'}
              onChange={(e) => onChange({ ...value, presetId: e.target.value, overrides: {}, trimMax: null })}
              disabled={disabled}
              className="px-3 py-1.5 text-sm bg-slate-700 border border-slate-600 rounded-lg
                         focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:opacity-50"
            >
              {config.presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.target_size_mb} MB)
                </option>
              ))}
              <option value="custom">Custom size</option>
            </select>

            {(value.presetId === 'custom' || !value.presetId) && (
              <label className="flex items-center gap-1 text-sm">
                <input
                  type="number"
                  min="0.1"
                  step="0.1"
                  inputMode="decimal"
                  value={value.customMb}
                  onChange={(e) => onChange({ ...value, customMb: e.target.value, overrides: {}, trimMax: null })}
                  disabled={disabled}
                  placeholder="MB"
                  className="w-20 px-2 py-1.5 bg-slate-700 border border-slate-600 rounded-lg
                             focus:outline-none focus:ring-2 focus:ring-primary-500"
                />
                MB
              </label>
            )}

            {hasOverrides && (
              <span className="flex items-center gap-1 px-2 py-1 text-xs bg-slate-700 rounded">
                {describeOverrides(value.overrides)}
                <button
                  onClick={() => onChange({ ...value, overrides: {}, trimMax: null })}
                  className="text-slate-400 hover:text-white"
                  title="Reset adjustments"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            )}
          </>
        )}
      </div>

      {value.enabled && options && value.trimMax && duration > 0 && value.overrides.trim_end != null && (
        <div className="text-xs max-w-md">
          <TrimSlider
            duration={duration}
            maxLength={value.trimMax}
            start={value.overrides.trim_start || 0}
            end={value.overrides.trim_end}
            disabled={disabled}
            onChange={(start, end) => onChange({
              ...value,
              overrides: { ...value.overrides, trim_start: start || undefined, trim_end: end },
            })}
          />
        </div>
      )}

      {value.enabled && options && (
        <div className="text-xs">
          {checking && !rejection && (
            <p className="flex items-center gap-1 text-slate-400">
              <Loader2 className="w-3 h-3 animate-spin" /> Checking server capacity…
            </p>
          )}

          {!checking && verdict?.admitted && (
            <p className="flex items-center gap-1 text-green-400">
              <CheckCircle2 className="w-3 h-3" />
              ≤ {verdict.target_mb} MB at {verdict.width}×{verdict.height}
              {` · ready in ~${formatSeconds(verdict.total_seconds)} after download`}
              {verdict.deferred && ' (deferred, may take longer)'}
            </p>
          )}

          {verdict && !verdict.admitted && (!checking || rejection) && (
            <div className="p-2 bg-amber-900/20 border border-amber-800/50 rounded-lg space-y-2">
              <p className="flex items-start gap-1 text-amber-400">
                <AlertTriangle className="w-3 h-3 mt-0.5 flex-shrink-0" />
                {verdict.message}
              </p>
              {verdict.alternatives?.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {verdict.alternatives.map((alt) => (
                    <button
                      key={`${alt.kind}-${alt.label}`}
                      onClick={() => onChange(valueFromOptions(value, alt.options))}
                      disabled={disabled}
                      className="px-2 py-1 bg-slate-700 hover:bg-slate-600 rounded text-slate-200
                                 transition-colors disabled:opacity-50"
                    >
                      {alt.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
