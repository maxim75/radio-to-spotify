import React, { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import { PlaylistsPage } from './components/PlaylistsPage';
import { SpotifyPlaylistsPage } from './components/SpotifyPlaylistsPage';

/**
 * Spotify connection state in the nav, so it is visible before a request fails
 * rather than only after one does.
 *
 * The link opens in a new tab because /callback ends on a plain "you can close this
 * window" page; navigating this tab there would leave the user stranded on it. On
 * returning here, "Check again" re-reads the status without a full reload.
 */
const SpotifyAuthStatus: React.FC = () => {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);

  const checkStatus = async () => {
    try {
      const response = await fetch('/spotify/status');
      const data = await response.json();
      setAuthenticated(Boolean(data.authenticated));
    } catch {
      // Leave it unknown rather than claiming "not connected": a network blip is not
      // the same as a missing token, and offering a login link would be misleading.
      setAuthenticated(null);
    }
  };

  useEffect(() => { checkStatus(); }, []);

  if (authenticated === null) {
    return null;
  }

  if (authenticated) {
    return (
      <span style={{ padding: '8px 12px', color: '#1db954', fontWeight: 'bold' }}>
        ● Spotify connected
      </span>
    );
  }

  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      <a
        href="/spotify/auth"
        target="_blank"
        rel="noopener noreferrer"
        style={{
          textDecoration: 'none',
          color: 'white',
          fontWeight: 'bold',
          padding: '8px 12px',
          borderRadius: '4px',
          backgroundColor: '#1db954'
        }}
      >
        Connect Spotify
      </a>
      <button
        onClick={checkStatus}
        style={{
          padding: '6px 10px',
          border: '1px solid #ccc',
          borderRadius: '4px',
          backgroundColor: '#fff',
          cursor: 'pointer'
        }}
      >
        Check again
      </button>
    </span>
  );
};

const App: React.FC = () => {
  return (
    <Router>
      <div style={{ padding: '20px' }}>
        <nav style={{ 
          marginBottom: '20px', 
          paddingBottom: '10px', 
          borderBottom: '1px solid #ddd' 
        }}>
          <h1 style={{ margin: '0 0 10px 0', color: '#1db954' }}>
            Radio to Spotify
          </h1>
          <div style={{ display: 'flex', gap: '20px' }}>
            <Link 
              to="/" 
              style={{ 
                textDecoration: 'none', 
                color: '#333', 
                fontWeight: 'bold',
                padding: '8px 12px',
                borderRadius: '4px',
                backgroundColor: '#f0f0f0'
              }}
            >
              Radio Playlists
            </Link>
            <Link 
              to="/spotify" 
              style={{ 
                textDecoration: 'none', 
                color: '#333', 
                fontWeight: 'bold',
                padding: '8px 12px',
                borderRadius: '4px',
                backgroundColor: '#f0f0f0'
              }}
            >
              Spotify Playlists
            </Link>
            <span style={{ marginLeft: 'auto' }}>
              <SpotifyAuthStatus />
            </span>
          </div>
        </nav>
        
        <Routes>
          <Route path="/" element={<PlaylistsPage />} />
          <Route path="/spotify" element={<SpotifyPlaylistsPage />} />
        </Routes>
      </div>
    </Router>
  );
};

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
