import {
  Loader2, CheckCircle2, XCircle, Clock, Download,
  Trash2, RefreshCw, Merge
} from 'lucide-react';

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
};

function TaskItem({ task, onDelete }) {
  const config = STATUS_CONFIG[task.status] || STATUS_CONFIG.pending;
  const StatusIcon = config.icon;

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
                {config.label}
                {task.speed && ` • ${task.speed}`}
                {task.eta && ` • ETA: ${task.eta}`}
              </p>
            </div>

            <button
              onClick={() => onDelete(task.task_id)}
              className="p-1 text-slate-500 hover:text-red-400 transition-colors"
              title="Remove task"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>

          {/* Progress bar */}
          {(task.status === 'downloading' || task.status === 'merging') && (
            <div className="mt-2">
              <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                <div
                  className={`h-full bg-primary-500 rounded-full transition-all duration-300
                              ${task.status === 'merging' ? 'progress-animated' : ''}`}
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

          {/* Completed file */}
          {task.status === 'completed' && task.filename && (
            <p className="mt-1 text-sm text-green-400 truncate">
              ✓ {task.filename}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default function TaskList({ tasks, onDelete, onClearCompleted, onRefresh }) {
  const activeTasks = tasks.filter(t => !['completed', 'failed'].includes(t.status));
  const completedTasks = tasks.filter(t => ['completed', 'failed'].includes(t.status));

  if (tasks.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Downloads</h3>
        <div className="flex items-center gap-2">
          <button
            onClick={onRefresh}
            className="p-2 text-slate-400 hover:text-white transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          {completedTasks.length > 0 && (
            <button
              onClick={onClearCompleted}
              className="text-sm text-slate-400 hover:text-white transition-colors"
            >
              Clear completed
            </button>
          )}
        </div>
      </div>

      {/* Active tasks */}
      {activeTasks.length > 0 && (
        <div className="space-y-2">
          {activeTasks.map((task) => (
            <TaskItem key={task.task_id} task={task} onDelete={onDelete} />
          ))}
        </div>
      )}

      {/* Completed tasks */}
      {completedTasks.length > 0 && (
        <div className="space-y-2">
          {activeTasks.length > 0 && (
            <p className="text-sm text-slate-500 mt-4">Completed</p>
          )}
          {completedTasks.map((task) => (
            <TaskItem key={task.task_id} task={task} onDelete={onDelete} />
          ))}
        </div>
      )}
    </div>
  );
}
