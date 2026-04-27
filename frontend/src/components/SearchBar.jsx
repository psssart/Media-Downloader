import { useState } from 'react';
import { Search, Loader2 } from 'lucide-react';

export default function SearchBar({ onSearch, loading }) {
  const [url, setUrl] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (url.trim() && !loading) {
      onSearch(url.trim());
    }
  };

  return (
    <form onSubmit={handleSubmit} className="w-full max-w-3xl mx-auto">
      <div className="relative flex items-center">
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Paste video URL (YouTube, Instagram, TikTok, etc.)"
          className="w-full px-4 py-4 pl-12 text-lg bg-slate-800 border border-slate-700 rounded-xl
                     focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent
                     placeholder:text-slate-500 transition-all"
          disabled={loading}
        />
        <Search className="absolute left-4 w-5 h-5 text-slate-500" />
        <button
          type="submit"
          disabled={!url.trim() || loading}
          className="absolute right-2 px-6 py-2 bg-primary-600 hover:bg-primary-700
                     disabled:bg-slate-700 disabled:cursor-not-allowed
                     rounded-lg font-medium transition-colors flex items-center gap-2"
        >
          {loading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading...
            </>
          ) : (
            'Search'
          )}
        </button>
      </div>
    </form>
  );
}
