const API_BASE = '/api';

class ApiService {
  async request(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const config = {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    };

    const response = await fetch(url, config);

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Request failed' }));
      throw new Error(error.detail || 'Request failed');
    }

    return response.json();
  }

  // Downloads
  async extractInfo(url) {
    return this.request('/downloads/extract', {
      method: 'POST',
      body: JSON.stringify({ url }),
    });
  }

  async startDownload(url, options = {}) {
    return this.request('/downloads/start', {
      method: 'POST',
      body: JSON.stringify({
        url,
        quality: options.quality || 'best',
        format_id: options.formatId,
        audio_only: options.audioOnly || false,
      }),
    });
  }

  async getTasks() {
    return this.request('/downloads/tasks');
  }

  async getTask(taskId) {
    return this.request(`/downloads/tasks/${taskId}`);
  }

  async deleteTask(taskId) {
    return this.request(`/downloads/tasks/${taskId}`, {
      method: 'DELETE',
    });
  }

  async clearCompletedTasks() {
    return this.request('/downloads/tasks', {
      method: 'DELETE',
    });
  }

  async getFiles() {
    return this.request('/downloads/files');
  }

  async deleteFile(filename) {
    return this.request(`/downloads/files/${encodeURIComponent(filename)}`, {
      method: 'DELETE',
    });
  }

  getDownloadUrl(filename) {
    return `${API_BASE}/downloads/files/${encodeURIComponent(filename)}`;
  }

  async getStats() {
    return this.request('/downloads/stats');
  }

  // Settings
  async getSettings() {
    return this.request('/settings');
  }

  async getCookiesStatus() {
    return this.request('/settings/cookies/status');
  }

  async uploadCookies(file) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE}/settings/cookies/upload`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(error.detail || 'Upload failed');
    }

    return response.json();
  }

  async pasteCookies(content) {
    return this.request('/settings/cookies/paste', {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
  }

  async deleteCookies() {
    return this.request('/settings/cookies', {
      method: 'DELETE',
    });
  }

  async triggerCleanup() {
    return this.request('/settings/cleanup', {
      method: 'POST',
    });
  }

  // Health
  async healthCheck() {
    return this.request('/health');
  }
}

export const api = new ApiService();
export default api;
