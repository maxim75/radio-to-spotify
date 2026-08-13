import React, { useEffect, useState } from 'react';
import { PlaylistFile } from '../types';
import { PlaylistItem } from './PlaylistItem';
import { PlaylistContainer, PlaylistList } from './styles';

export const PlaylistsPage: React.FC = () => {
  const [files, setFiles] = useState<PlaylistFile[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  useEffect(() => {
    fetchPlaylists();
  }, []);

  const fetchPlaylists = async () => {
    try {
      setIsLoading(true);
      const response = await fetch('/api/playlists');
      const data = await response.json();
      if (data.status === 'success') {
        setFiles(data.playlists.map((name: string) => ({ name })));
      } else {
        console.error('Error from server:', data.message);
      }
    } catch (error) {
      console.error('Error fetching playlists:', error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <PlaylistContainer>
      <h1>Radio Playlists</h1>
      <PlaylistList>
        {files.map((file) => (
          <PlaylistItem key={file.name} file={file} />
        ))}
        {isLoading && (
          <li>Playlists loading...</li>
        )}
        {!isLoading && files.length === 0 && (
          <li>No playlist files found.</li>
        )}
      </PlaylistList>
    </PlaylistContainer>
  );
};
