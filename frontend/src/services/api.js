const API_BASE = '/api';

const STORAGE_KEY = 'md_client_id';
const COOKIE_NAME = 'md_client_id';

function getClientId() {
  // Try localStorage first
  let clientId = localStorage.getItem(STORAGE_KEY);

  // Fallback to cookie if localStorage was cleared
  if (!clientId) {
    const match = document.cookie.match(new RegExp(`(?:^|; )${COOKIE_NAME}=([^;]+)`));
    if (match) {
      clientId = match[1];
      localStorage.setItem(STORAGE_KEY, clientId);
    }
  }

  // Generate new if neither exists
  if (!clientId) {
    clientId = crypto.randomUUID();
    localStorage.setItem(STORAGE_KEY, clientId);
  }

  // Always refresh the cookie (1-year expiry)
  document.cookie = `${COOKIE_NAME}=${clientId}; path=/; max-age=31536000; SameSite=Strict`;

  return clientId;
}

class ApiService {
  async request(endpoint, options = {}) {
    const url = `${API_BASE}${endpoint}`;
    const config = {
      headers: {
        'Content-Type': 'application/json',
        'X-Client-ID': getClientId(),
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
        media_type: options.mediaType || 'video',
        source_url: options.sourceUrl || null,
        title: options.title || null,
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
    return `${API_BASE}/downloads/files/${encodeURIComponent(filename)}?client_id=${getClientId()}`;
  }

  getThumbnailUrl(filename) {
    return `${API_BASE}/downloads/thumbnails/${encodeURIComponent(filename)}?client_id=${getClientId()}`;
  }

  getProxyThumbnailUrl(url) {
    return `${API_BASE}/downloads/proxy-thumbnail?url=${encodeURIComponent(url)}`;
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
      headers: {
        'X-Client-ID': getClientId(),
      },
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

export { getClientId };
export const api = new ApiService();
export default api;
