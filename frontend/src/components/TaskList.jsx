import {
  Loader2, CheckCircle2, XCircle, Clock, Download,
  RefreshCw, Merge, Hourglass, Shrink, Ban, X, Scissors
} from 'lucide-react';

const TERMINAL = ['completed', 'failed', 'cancelled'];

const STATUS_CONFIG = {
  pending: {
    icon: Clock,
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-900/20',
    label: 'Pending'
  },
  processing: {
    icon: Loader2,
    color: 'text-blue-400',
    bgColor: 'bg-blue-900/20',
    label: 'Processing',
    animate: true
  },
  downloading: {
    icon: Download,
    color: 'text-primary-400',
    bgColor: 'bg-primary-900/20',
    label: 'Downloading'
  },
  merging: {
    icon: Merge,
    color: 'text-purple-400',
    bgColor: 'bg-purple-900/20',
    label: 'Merging'
  },
  trimming: {
    icon: Scissors,
    color: 'text-purple-400',
    bgColor: 'bg-purple-900/20',
    label: 'Trimming'
  },
  queued: {
    icon: Hourglass,
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-900/20',
    label: 'Waiting for encoder'
  },
  compressing: {
    icon: Shrink,
    color: 'text-purple-400',
    bgColor: 'bg-purple-900/20',
    label: 'Encoding'
  },
  completed: {
    icon: CheckCircle2,
    color: 'text-green-400',
    bgColor: 'bg-green-900/20',
    label: 'Completed'
  },
  failed: {
    icon: XCircle,
    color: 'text-red-400',
    bgColor: 'bg-red-900/20',
    label: 'Failed'
  },
  cancelled: {
    icon: Ban,
    color: 'text-slate-400',
    bgColor: 'bg-slate-800/40',
    label: 'Cancelled'
  },
};

function statusLabel(task, config) {
  if (task.status === 'queued' && task.queue_position) {
    return `${config.label} · #${task.queue_position} in queue`;
  }
  if (task.status === 'compressing' && task.compression_pass) {
    return `${config.label} · pass ${task.compression_pass}/2`;
  }
  return config.label;
}

function TaskItem({ task, onCancel }) {
  const config = STATUS_CONFIG[task.status] || STATUS_CONFIG.pending;
  const StatusIcon = config.icon;
  const cancellable = !TERMINAL.includes(task.status);

  return (
    <div className={`p-4 rounded-lg border border-slate-700 ${config.bgColor}`}>
      <div className="flex items-start gap-3">
        <StatusIcon
          className={`w-5 h-5 mt-0.5 flex-shrink-0 ${config.color} ${config.animate ? 'animate-spin' : ''}`}
        />

        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="font-medium truncate">
                {task.title || task.url}
              </p>
              <p className="text-sm text-slate-400">
                {statusLabel(task, config)}
                {task.speed && ` • ${task.speed}`}
                {task.eta && ` • ETA: ${task.eta}`}
              </p>
            </div>

            {cancellable && (
              <button
                onClick={() => onCancel(task.task_id)}
                className="p-1 text-slate-500 hover:text-yellow-400 transition-colors"
                title="Cancel"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>

          {/* Progress bar */}
          {['downloading', 'merging', 'trimming', 'compressing'].includes(task.status) && (
            <div className="mt-2">
              <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                <div
                  className={`h-full bg-primary-500 rounded-full transition-all duration-300
                              ${['merging', 'trimming'].includes(task.status) ? 'progress-animated' : ''}`}
                  style={{ width: `${task.progress}%` }}
                />
              </div>
              <p className="text-xs text-slate-500 mt-1">
                {task.progress.toFixed(1)}%
              </p>
            </div>
          )}

          {/* Error message */}
          {task.error && (
            <p className="mt-2 text-sm text-red-400">
              Error: {task.error}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// Finished tasks are removed by App; only active ones and recent failures arrive here
export default function TaskList({ tasks, onCancel, onRefresh }) {
  if (tasks.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Downloads</h3>
        <button
          onClick={onRefresh}
          className="p-2 text-slate-400 hover:text-white transition-colors"
          title="Refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <div className="space-y-2">
        {tasks.map((task) => (
          <TaskItem key={task.task_id} task={task} onCancel={onCancel} />
        ))}
      </div>
    </div>
  );
}
