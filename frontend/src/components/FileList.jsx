import { Download, Trash2, FileVideo, FileAudio, FileImage, File, HardDrive } from 'lucide-react';
import api from '../services/api';

function formatFileSize(bytes) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatDate(dateString) {
  return new Date(dateString).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getFileIcon(filename) {
  const ext = filename.split('.').pop().toLowerCase();
  if (['mp4', 'webm', 'mkv', 'avi', 'mov'].includes(ext)) {
    return FileVideo;
  }
  if (['mp3', 'wav', 'flac', 'm4a', 'ogg'].includes(ext)) {
    return FileAudio;
  }
  if (['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(ext)) {
    return FileImage;
  }
  return File;
}

function FileItem({ file, onDelete }) {
  const FileIcon = getFileIcon(file.filename);

  const handleDownload = () => {
    const link = document.createElement('a');
    link.href = api.getDownloadUrl(file.filename);
    link.download = file.filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className="flex items-center gap-3 p-3 bg-slate-800 rounded-lg border border-slate-700
                    hover:border-slate-600 transition-colors group">
      {file.thumbnail_url ? (
        <img
          src={file.thumbnail_url}
          alt=""
          className="w-8 h-8 rounded object-cover flex-shrink-0"
        />
      ) : (
        <FileIcon className="w-8 h-8 text-slate-400 flex-shrink-0" />
      )}

      <div className="flex-1 min-w-0">
        <p className="font-medium truncate" title={file.filename}>
          {file.filename}
        </p>
        <p className="text-sm text-slate-500">
          {formatFileSize(file.size)} • {formatDate(file.created_at)}
        </p>
      </div>

      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={handleDownload}
          className="p-2 text-slate-400 hover:text-primary-400 transition-colors"
          title="Download"
        >
          <Download className="w-5 h-5" />
        </button>
        <button
          onClick={() => onDelete(file.filename)}
          className="p-2 text-slate-400 hover:text-red-400 transition-colors"
          title="Delete"
        >
          <Trash2 className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}

export default function FileList({ files, stats, onDelete, onRefresh }) {
  if (files.length === 0) {
    return (
      <div className="text-center py-8 text-slate-500">
        <HardDrive className="w-12 h-12 mx-auto mb-2 opacity-50" />
        <p>No downloaded files</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <HardDrive className="w-5 h-5" />
          Downloaded Files
        </h3>
        {stats && (
          <span className="text-sm text-slate-400">
            {stats.file_count} files • {stats.total_size_mb} MB
          </span>
        )}
      </div>

      <div className="space-y-2">
        {files.map((file) => (
          <FileItem
            key={file.filename}
            file={file}
            onDelete={onDelete}
          />
        ))}
      </div>
    </div>
  );
}
