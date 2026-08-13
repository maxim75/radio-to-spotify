import React, { useEffect, useState } from 'react';
import { SpotifyPlaylist, MergeProgress } from '../types';
import { PlaylistContainer, PlaylistList, PlaylistActions, MergeButton, DropdownContainer, DropdownMenu, DropdownItem } from './styles';
import { ProgressBar } from './ProgressBar';

export const SpotifyPlaylistsPage: React.FC = () => {
  const [playlists, setPlaylists] = useState<SpotifyPlaylist[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [itemsPerPage, setItemsPerPage] = useState<number>(20);
  const [mergeProgress, setMergeProgress] = useState<{ [playlistId: string]: MergeProgress }>({});
  const [dropdownOpen, setDropdownOpen] = useState<string | null>(null);
  const [mergingPlaylists, setMergingPlaylists] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchSpotifyPlaylists();
  }, []);

  const fetchSpotifyPlaylists = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await fetch('/spotify_playlists');
      const data = await response.json();

      if (data.status === 'success') {
        setPlaylists(data.playlists);
      } else {
        setError(data.message || 'Failed to fetch Spotify playlists');
        console.error('Error from server:', data.message);
      }
    } catch (error) {
      setError('Network error while fetching Spotify playlists');
      console.error('Error fetching Spotify playlists:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const getImageUrl = (playlist: SpotifyPlaylist) => {
    if (playlist.images && playlist.images.length > 0) {
      return playlist.images[0].url;
    }
    return '/static/placeholder-album.png';
  };

  const openSpotifyPlaylist = (externalUrl: string) => {
    window.open(externalUrl, '_blank');
  };

  // Calculate pagination
  const totalPages = Math.ceil(playlists.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const endIndex = startIndex + itemsPerPage;
  const currentPlaylists = playlists.slice(startIndex, endIndex);

  const handlePageChange = (page: number) => {
    setCurrentPage(page);
  };

  const handleItemsPerPageChange = (newItemsPerPage: number) => {
    setItemsPerPage(newItemsPerPage);
    setCurrentPage(1); // Reset to first page when changing items per page
  };

  const handleMergeClick = (e: React.MouseEvent, playlistId: string) => {
    e.stopPropagation(); // Prevent opening the playlist
    setDropdownOpen(dropdownOpen === playlistId ? null : playlistId);
  };

  const handlePlaylistSelect = async (e: React.MouseEvent, sourcePlaylistId: string, targetPlaylistId: string) => {
    e.stopPropagation();
    setDropdownOpen(null);
    
    // Don't merge with itself
    if (sourcePlaylistId === targetPlaylistId) {
      return;
    }

    setMergingPlaylists(prev => new Set(prev).add(sourcePlaylistId));
    
    try {
      const response = await fetch('/merge_playlists', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
        body: JSON.stringify({
          source_playlist_id: sourcePlaylistId,
          target_playlist_id: targetPlaylistId
        })
      });

      const data = await response.json();
      
      if (data.status === 'success') {
        const taskId = data.task_id;
        pollMergeProgress(taskId, sourcePlaylistId);
      } else {
        setMergeProgress(prev => ({
          ...prev,
          [sourcePlaylistId]: {
            status: 'error',
            progress: 0,
            message: data.message
          }
        }));
        setMergingPlaylists(prev => {
          const newSet = new Set(prev);
          newSet.delete(sourcePlaylistId);
          return newSet;
        });
      }
    } catch (error) {
      setMergeProgress(prev => ({
        ...prev,
        [sourcePlaylistId]: {
          status: 'error',
          progress: 0,
          message: 'An error occurred while merging playlists'
        }
      }));
      setMergingPlaylists(prev => {
        const newSet = new Set(prev);
        newSet.delete(sourcePlaylistId);
        return newSet;
      });
    }
  };

  const pollMergeProgress = async (taskId: string, playlistId: string) => {
    const poll = setInterval(async () => {
      try {
        const response = await fetch(`/playlist_progress/${taskId}`);
        const data = await response.json();

        setMergeProgress(prev => ({
          ...prev,
          [playlistId]: {
            status: data.status,
            progress: data.progress,
            message: data.message
          }
        }));

        if (data.status === 'completed' || data.status === 'error') {
          clearInterval(poll);
          setMergingPlaylists(prev => {
            const newSet = new Set(prev);
            newSet.delete(playlistId);
            return newSet;
          });
          
          // Clear progress after 5 seconds
          setTimeout(() => {
            setMergeProgress(prev => {
              const newProgress = { ...prev };
              delete newProgress[playlistId];
              return newProgress;
            });
          }, 5000);
        }
      } catch (error) {
        clearInterval(poll);
        setMergeProgress(prev => ({
          ...prev,
          [playlistId]: {
            status: 'error',
            progress: 0,
            message: 'Error checking merge progress'
          }
        }));
        setMergingPlaylists(prev => {
          const newSet = new Set(prev);
          newSet.delete(playlistId);
          return newSet;
        });
      }
    }, 1000);
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = () => setDropdownOpen(null);
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, []);

  return (
    <PlaylistContainer>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h1 style={{ margin: 0 }}>My Spotify Playlists</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <label htmlFor="items-per-page" style={{ fontSize: '14px', color: '#666' }}>
            Items per page:
          </label>
          <select
            id="items-per-page"
            value={itemsPerPage}
            onChange={(e) => handleItemsPerPageChange(Number(e.target.value))}
            style={{
              padding: '4px 8px',
              border: '1px solid #ddd',
              borderRadius: '4px',
              fontSize: '14px'
            }}
          >
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </div>

      {error && (
        <div style={{
          backgroundColor: '#fee',
          border: '1px solid #fcc',
          padding: '12px',
          borderRadius: '4px',
          marginBottom: '20px',
          color: '#c00'
        }}>
          <strong>Error:</strong> {error}
          <button
            onClick={fetchSpotifyPlaylists}
            style={{
              marginLeft: '10px',
              padding: '4px 8px',
              backgroundColor: '#c00',
              color: 'white',
              border: 'none',
              borderRadius: '3px',
              cursor: 'pointer'
            }}
          >
            Retry
          </button>
        </div>
      )}

      {!isLoading && !error && (
        <div style={{ marginBottom: '20px', fontSize: '14px', color: '#666' }}>
          Showing {currentPlaylists.length} of {playlists.length} playlists
        </div>
      )}

      <PlaylistList>
        {isLoading && (
          <li>Loading Spotify playlists...</li>
        )}

        {!isLoading && !error && playlists.length === 0 && (
          <li>No Spotify playlists found.</li>
        )}

        {!isLoading && !error && currentPlaylists.map((playlist) => (
          <li
            key={playlist.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              padding: '12px',
              border: '1px solid #ddd',
              borderRadius: '8px',
              marginBottom: '8px',
              backgroundColor: '#f9f9f9',
              cursor: 'pointer',
              transition: 'background-color 0.2s'
            }}
            onClick={() => openSpotifyPlaylist(playlist.external_url)}
            onMouseEnter={(e) => e.currentTarget.style.backgroundColor = '#e9e9e9'}
            onMouseLeave={(e) => e.currentTarget.style.backgroundColor = '#f9f9f9'}
          >
            <img
              src={getImageUrl(playlist)}
              alt={playlist.name}
              style={{
                width: '60px',
                height: '60px',
                borderRadius: '4px',
                marginRight: '12px',
                objectFit: 'cover',
                backgroundColor: '#e0e0e0'
              }}
              onError={(e) => {
                (e.target as HTMLImageElement).src = '/static/placeholder-album.png';
              }}
            />

            <div style={{ flex: 1 }}>
              <h3 style={{ margin: '0 0 4px 0', fontSize: '16px', fontWeight: 'bold' }}>
                {playlist.name}
              </h3>
              <p style={{ margin: '0 0 4px 0', fontSize: '14px', color: '#666' }}>
                {playlist.description || 'No description'}
              </p>
              <div style={{ fontSize: '12px', color: '#888' }}>
                <span>By {playlist.owner}</span>
                <span style={{ marginLeft: '12px' }}>
                  {playlist.tracks_total} tracks
                </span>
                <span style={{ marginLeft: '12px' }}>
                  {playlist.public ? 'Public' : 'Private'}
                  {playlist.collaborative && ' • Collaborative'}
                </span>
              </div>
            </div>

            <PlaylistActions>
              <DropdownContainer>
                <MergeButton
                  onClick={(e) => handleMergeClick(e, playlist.id)}
                  disabled={mergingPlaylists.has(playlist.id)}
                >
                  {mergingPlaylists.has(playlist.id) ? 'Merging...' : 'Merge'}
                </MergeButton>
                <DropdownMenu isOpen={dropdownOpen === playlist.id}>
                  {playlists
                    .filter(p => p.id !== playlist.id) // Don't show self in dropdown
                    .map(targetPlaylist => (
                      <DropdownItem
                        key={targetPlaylist.id}
                        onClick={(e) => handlePlaylistSelect(e, playlist.id, targetPlaylist.id)}
                      >
                        <div className="playlist-name">{targetPlaylist.name}</div>
                        <div className="playlist-details">
                          {targetPlaylist.tracks_total} tracks • By {targetPlaylist.owner}
                        </div>
                      </DropdownItem>
                    ))}
                </DropdownMenu>
              </DropdownContainer>
              
              {mergeProgress[playlist.id] && (
                <div style={{ marginLeft: '10px', minWidth: '200px' }}>
                  <ProgressBar 
                    active={mergeProgress[playlist.id].status === 'processing'} 
                    progress={mergeProgress[playlist.id]}
                  />
                </div>
              )}
              
              <div 
                style={{ fontSize: '12px', color: '#1db954', cursor: 'pointer' }}
                onClick={(e) => {
                  e.stopPropagation();
                  openSpotifyPlaylist(playlist.external_url);
                }}
              >
                Open in Spotify →
              </div>
            </PlaylistActions>
          </li>
        ))}
      </PlaylistList>

      {!isLoading && !error && totalPages > 1 && (
        <div style={{ 
          display: 'flex', 
          justifyContent: 'center', 
          alignItems: 'center',
          marginTop: '20px',
          gap: '10px'
        }}>
          <button
            onClick={() => handlePageChange(currentPage - 1)}
            disabled={currentPage === 1}
            style={{
              padding: '8px 12px',
              border: '1px solid #ddd',
              borderRadius: '4px',
              backgroundColor: currentPage === 1 ? '#f0f0f0' : '#fff',
              cursor: currentPage === 1 ? 'not-allowed' : 'pointer',
              fontSize: '14px'
            }}
          >
            Previous
          </button>

          <span style={{ fontSize: '14px', color: '#666' }}>
            Page {currentPage} of {totalPages}
          </span>

          <button
            onClick={() => handlePageChange(currentPage + 1)}
            disabled={currentPage === totalPages}
            style={{
              padding: '8px 12px',
              border: '1px solid #ddd',
              borderRadius: '4px',
              backgroundColor: currentPage === totalPages ? '#f0f0f0' : '#fff',
              cursor: currentPage === totalPages ? 'not-allowed' : 'pointer',
              fontSize: '14px'
            }}
          >
            Next
          </button>
        </div>
      )}
    </PlaylistContainer>
  );
};
