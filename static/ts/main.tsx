import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom';
import { PlaylistsPage } from './components/PlaylistsPage';
import { SpotifyPlaylistsPage } from './components/SpotifyPlaylistsPage';

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
