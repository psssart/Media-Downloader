import { useState, useEffect, useCallback, useRef } from 'react';
import { Download, Settings, AlertCircle, RefreshCw } from 'lucide-react';
import SearchBar from './components/SearchBar';
import MediaCard from './components/MediaCard';
import TaskList from './components/TaskList';
import FileList from './components/FileList';
import SettingsPanel from './components/SettingsPanel';
import api from './services/api';

// Finished tasks leave the list on their own; a failure stays long enough to read the error
const FAILED_VISIBLE_MS = 15000;

// Compression reports of finished tasks, shown next to their files (kept per browser)
const REPORTS_KEY = 'md_compression_reports';

function loadReports() {
  try {
    return JSON.parse(localStorage.getItem(REPORTS_KEY)) || {};
  } catch {
    return {};
  }
}

function saveReports(reports) {
  try {
    localStorage.setItem(REPORTS_KEY, JSON.stringify(reports));
  } catch {
    // storage unavailable: reports live until reload
  }
}

function App() {
  const [media, setMedia] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [files, setFiles] = useState([]);
  const [stats, setStats] = useState(null);
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const [reports, setReports] = useState(loadReports);
  const dismissed = useRef(new Set());

  const updateReports = useCallback((update) => {
    setReports(prev => {
      const next = update(prev);
      if (next !== prev) saveReports(next);
      return next;
    });
  }, []);

  // Fetch files
  const fetchFiles = useCallback(async () => {
    try {
      const [filesData, statsData] = await Promise.all([
        api.getFiles(),
        api.getStats(),
      ]);
      setFiles(filesData);
      setStats(statsData);
      // Forget reports of files that are gone (the grace period covers a listing
      // fetched just before the task finished)
      const names = new Set(filesData.map(f => f.filename));
      updateReports(prev => {
        const stale = Object.keys(prev).filter(
          name => !names.has(name) && Date.now() - prev[name].at > 60000);
        if (stale.length === 0) return prev;
        const next = { ...prev };
        stale.forEach(name => delete next[name]);
        return next;
      });
    } catch (err) {
      console.error('Failed to fetch files:', err);
    }
  }, [updateReports]);

  // Fetch tasks periodically; finished ones are removed from the server and the list
  const fetchTasks = useCallback(async () => {
    try {
      const data = await api.getTasks();
      let completed = false;
      for (const task of data) {
        if (!['completed', 'failed', 'cancelled'].includes(task.status)) continue;
        if (dismissed.current.has(task.task_id)) continue;
        dismissed.current.add(task.task_id);
        if (task.status === 'completed') completed = true;
        if (task.status === 'completed' && task.compression_result && task.filename) {
          updateReports(prev => ({
            ...prev, [task.filename]: { ...task.compression_result, at: Date.now() },
          }));
        }
        setTimeout(() => {
          api.deleteTask(task.task_id).catch(() => {});
          setTasks(prev => prev.filter(t => t.task_id !== task.task_id));
        }, task.status === 'failed' ? FAILED_VISIBLE_MS : 0);
      }
      setTasks(data.filter(t => !['completed', 'cancelled'].includes(t.status)));
      if (completed) fetchFiles();
    } catch (err) {
      console.error('Failed to fetch tasks:', err);
    }
  }, [fetchFiles, updateReports]);

  // Fetch settings
  const fetchSettings = useCallback(async () => {
    try {
      const data = await api.getSettings();
      setSettings(data);
    } catch (err) {
      console.error('Failed to fetch settings:', err);
    }
  }, []);

  // Initial load and polling
  useEffect(() => {
    // Clear stale completed/failed tasks from previous sessions
    api.clearCompletedTasks().then(() => fetchTasks());
    fetchFiles();
    fetchSettings();

    // Poll for task updates
    const taskInterval = setInterval(fetchTasks, 2000);
    const fileInterval = setInterval(fetchFiles, 10000);

    return () => {
      clearInterval(taskInterval);
      clearInterval(fileInterval);
    };
  }, [fetchTasks, fetchFiles, fetchSettings]);

  // Handle URL search
  const handleSearch = async (url) => {
    setLoading(true);
    setError(null);
    setMedia(null);

    try {
      const info = await api.extractInfo(url);
      setMedia(info);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Handle download start
  const handleDownload = async (options) => {
    setDownloading(true);
    setError(null);

    try {
      await api.startDownload(options.url, {
        quality: options.quality,
        audioOnly: options.audioOnly,
        mediaType: options.mediaType || 'video',
        sourceUrl: options.sourceUrl,
        title: options.title,
        compression: options.compression,
        trim: options.trim,
      });
      fetchTasks();
      return null;
    } catch (err) {
      // Compression admission rejections carry alternatives; the card shows them
      if (err.data?.alternatives) return { rejection: err.data };
      setError(err.message);
      return null;
    } finally {
      setDownloading(false);
    }
  };

  // Handle task cancellation
  const handleCancelTask = async (taskId) => {
    try {
      await api.cancelTask(taskId);
      fetchTasks();
    } catch (err) {
      console.error('Failed to cancel task:', err);
    }
  };

  // Handle file deletion
  const handleDeleteFile = async (filename) => {
    try {
      await api.deleteFile(filename);
      fetchFiles();
    } catch (err) {
      console.error('Failed to delete file:', err);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900">
      {/* Header */}
      <header className="bg-slate-800 border-b border-slate-700 sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
          <h1 className="text-xl font-bold flex items-center gap-2">
            <Download className="w-6 h-6 text-primary-500" />
            Media Downloader
          </h1>
          <button
            onClick={() => setSettingsOpen(true)}
            className="p-2 text-slate-400 hover:text-white hover:bg-slate-700
                       rounded-lg transition-colors"
            title="Settings"
          >
            <Settings className="w-5 h-5" />
          </button>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-6xl mx-auto px-4 py-8 space-y-8">
        {/* Search section */}
        <section className="text-center space-y-6">
          <div>
            <h2 className="text-3xl font-bold mb-2">Download Videos, Audio & Photos</h2>
            <p className="text-slate-400">
              Paste a URL from YouTube, Instagram, TikTok, and 1000+ other sites
            </p>
          </div>
          <SearchBar onSearch={handleSearch} loading={loading} />
        </section>

        {/* Error display */}
        {error && (
          <div className="max-w-3xl mx-auto p-4 bg-red-900/30 border border-red-800
                          rounded-lg flex items-center gap-3 text-red-400">
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
            <p>{error}</p>
          </div>
        )}

        {/* Media card */}
        {media && (
          <section className="max-w-4xl mx-auto">
            <MediaCard
              media={media}
              onDownload={handleDownload}
              downloading={downloading}
            />
          </section>
        )}

        {/* Tasks and files grid */}
        <div className="grid lg:grid-cols-2 gap-8">
          {/* Tasks */}
          <section className="bg-slate-800/50 rounded-xl p-6 border border-slate-700 min-w-0">
            <TaskList
              tasks={tasks}
              onCancel={handleCancelTask}
              onRefresh={fetchTasks}
            />
            {tasks.length === 0 && (
              <div className="text-center py-8 text-slate-500">
                <Download className="w-12 h-12 mx-auto mb-2 opacity-50" />
                <p>No active downloads</p>
                <p className="text-sm">Start by searching for a video above</p>
              </div>
            )}
          </section>

          {/* Files */}
          <section className="bg-slate-800/50 rounded-xl p-6 border border-slate-700 min-w-0">
            <FileList
              files={files}
              stats={stats}
              reports={reports}
              onDelete={handleDeleteFile}
              onRefresh={fetchFiles}
            />
          </section>
        </div>

        {/* Supported platforms */}
        <section className="text-center py-8 border-t border-slate-800">
          <p className="text-sm text-slate-500 mb-3">Supported platforms</p>
          <div className="flex flex-wrap justify-center gap-4 text-slate-400 text-sm">
            {['YouTube', 'Instagram', 'TikTok', 'Twitter/X', 'Facebook', 'Vimeo', 'Reddit', 'Twitch', '1000+ more'].map((platform) => (
              <span key={platform} className="px-3 py-1 bg-slate-800 rounded-full">
                {platform}
              </span>
            ))}
          </div>
        </section>
      </main>

      {/* Settings panel */}
      <SettingsPanel
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        settings={settings}
        onSettingsChange={fetchSettings}
      />
    </div>
  );
}

export default App;
