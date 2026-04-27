import { useState, useRef } from 'react';
import {
  Settings, Cookie, Upload, Trash2, Check, AlertCircle,
  X, FileText, Loader2
} from 'lucide-react';
import api from '../services/api';

export default function SettingsPanel({ isOpen, onClose, settings, onSettingsChange }) {
  const [cookieContent, setCookieContent] = useState('');
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState(null);
  const fileInputRef = useRef(null);

  const showMessage = (text, type = 'success') => {
    setMessage({ text, type });
    setTimeout(() => setMessage(null), 3000);
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    try {
      await api.uploadCookies(file);
      showMessage('Cookies uploaded successfully');
      onSettingsChange();
    } catch (error) {
      showMessage(error.message, 'error');
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handlePasteCookies = async () => {
    if (!cookieContent.trim()) return;

    setUploading(true);
    try {
      await api.pasteCookies(cookieContent);
      showMessage('Cookies saved successfully');
      setCookieContent('');
      onSettingsChange();
    } catch (error) {
      showMessage(error.message, 'error');
    } finally {
      setUploading(false);
    }
  };

  const handleDeleteCookies = async () => {
    try {
      await api.deleteCookies();
      showMessage('Cookies deleted');
      onSettingsChange();
    } catch (error) {
      showMessage(error.message, 'error');
    }
  };

  const handleCleanup = async () => {
    try {
      const result = await api.triggerCleanup();
      showMessage(result.message);
    } catch (error) {
      showMessage(error.message, 'error');
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-slate-800 rounded-xl border border-slate-700 w-full max-w-lg max-h-[90vh] overflow-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-slate-700">
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <Settings className="w-5 h-5" />
            Settings
          </h2>
          <button
            onClick={onClose}
            className="p-1 text-slate-400 hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-6">
          {/* Message */}
          {message && (
            <div className={`p-3 rounded-lg flex items-center gap-2 ${
              message.type === 'error'
                ? 'bg-red-900/50 text-red-400'
                : 'bg-green-900/50 text-green-400'
            }`}>
              {message.type === 'error' ? (
                <AlertCircle className="w-4 h-4" />
              ) : (
                <Check className="w-4 h-4" />
              )}
              {message.text}
            </div>
          )}

          {/* Cookie Management */}
          <div className="space-y-4">
            <h3 className="font-medium flex items-center gap-2">
              <Cookie className="w-4 h-4" />
              Cookie Management
            </h3>

            <p className="text-sm text-slate-400">
              Upload a cookies.txt file to access age-restricted or private content
              from platforms like YouTube, Instagram, and Facebook.
            </p>

            {/* Current status */}
            <div className={`p-3 rounded-lg ${
              settings?.cookies_configured
                ? 'bg-green-900/30 border border-green-800'
                : 'bg-slate-700/50 border border-slate-600'
            }`}>
              <div className="flex items-center justify-between">
                <span className="text-sm">
                  {settings?.cookies_configured
                    ? '✓ Cookies configured'
                    : 'No cookies configured'}
                </span>
                {settings?.cookies_configured && (
                  <button
                    onClick={handleDeleteCookies}
                    className="text-sm text-red-400 hover:text-red-300 flex items-center gap-1"
                  >
                    <Trash2 className="w-3 h-3" />
                    Remove
                  </button>
                )}
              </div>
            </div>

            {/* File upload */}
            <div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt"
                onChange={handleFileUpload}
                className="hidden"
                id="cookie-upload"
              />
              <label
                htmlFor="cookie-upload"
                className="flex items-center justify-center gap-2 p-3 border-2 border-dashed
                           border-slate-600 rounded-lg cursor-pointer hover:border-primary-500
                           hover:bg-slate-700/50 transition-colors"
              >
                {uploading ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : (
                  <Upload className="w-5 h-5" />
                )}
                <span>Upload cookies.txt file</span>
              </label>
            </div>

            {/* Or paste content */}
            <div className="space-y-2">
              <p className="text-sm text-slate-400">Or paste cookie content:</p>
              <textarea
                value={cookieContent}
                onChange={(e) => setCookieContent(e.target.value)}
                placeholder="Paste your cookies.txt content here..."
                rows={4}
                className="w-full px-3 py-2 bg-slate-700 border border-slate-600 rounded-lg
                           focus:outline-none focus:ring-2 focus:ring-primary-500
                           placeholder:text-slate-500 text-sm font-mono"
              />
              <button
                onClick={handlePasteCookies}
                disabled={!cookieContent.trim() || uploading}
                className="px-4 py-2 bg-primary-600 hover:bg-primary-700 disabled:bg-slate-700
                           disabled:cursor-not-allowed rounded-lg text-sm font-medium transition-colors"
              >
                Save Cookies
              </button>
            </div>

            {/* Help text */}
            <div className="p-3 bg-slate-700/50 rounded-lg">
              <p className="text-sm text-slate-400 flex items-start gap-2">
                <FileText className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>
                  Export cookies from your browser using extensions like
                  "Get cookies.txt" or "EditThisCookie". The file must be in
                  Netscape cookie format.
                </span>
              </p>
            </div>
          </div>

          {/* Storage Management */}
          <div className="space-y-4 pt-4 border-t border-slate-700">
            <h3 className="font-medium">Storage Management</h3>

            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm">File retention</p>
                <p className="text-xs text-slate-500">
                  Files are automatically deleted after {settings?.file_retention_hours || 24} hours
                </p>
              </div>
              <button
                onClick={handleCleanup}
                className="px-3 py-1 text-sm bg-slate-700 hover:bg-slate-600
                           rounded transition-colors"
              >
                Clean Now
              </button>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-slate-700 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg
                       font-medium transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
