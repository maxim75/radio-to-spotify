import React, { useState } from 'react';
import { PlaylistFile, PlaylistProgress } from '../types';
import { ProgressBar } from './ProgressBar';
import {
  PlaylistItem as StyledPlaylistItem,
  PlaylistName,
  ButtonGroup,
  ViewButton,
  AddButton,
} from './styles';

interface PlaylistItemProps {
  file: PlaylistFile;
}

export const PlaylistItem: React.FC<PlaylistItemProps> = ({ file }) => {
  const [isProcessing, setIsProcessing] = useState(false);
  const [progress, setProgress] = useState<PlaylistProgress>({
    status: 'processing',
    progress: 0,
    message: 'Initializing...'
  });

  const handleAddToSpotify = async () => {
    setIsProcessing(true);
    try {
      const response = await fetch('/create_playlist_from_file', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json'
        },
        body: JSON.stringify({ file_name: file.name })
      });

      const data = await response.json();
      
      if (data.status === 'success') {
        const taskId = data.task_id;
        pollProgress(taskId);
      } else {
        setProgress({
          status: 'error',
          progress: 0,
          message: data.message
        });
        setIsProcessing(false);
      }
    } catch (error) {
      setProgress({
        status: 'error',
        progress: 0,
        message: 'An error occurred while creating the playlist'
      });
      setIsProcessing(false);
    }
  };

  const pollProgress = async (taskId: string) => {
    const poll = setInterval(async () => {
      try {
        const response = await fetch(`/playlist_progress/${taskId}`);
        const data = await response.json();

        setProgress({
          status: data.status,
          progress: data.progress,
          message: data.message
        });

        if (data.status === 'completed' || data.status === 'error') {
          clearInterval(poll);
          setIsProcessing(false);
        }
      } catch (error) {
        clearInterval(poll);
        setIsProcessing(false);
        setProgress({
          status: 'error',
          progress: 0,
          message: 'Error checking progress'
        });
      }
    }, 1000);
  };

  return (
    <StyledPlaylistItem>
      <PlaylistName>{file.name}</PlaylistName>
      <ButtonGroup>
        <ViewButton href={`/playlists/view/${file.name}`}>View</ViewButton>
        <AddButton
          onClick={handleAddToSpotify}
          disabled={isProcessing}
        >
          Add to Spotify
        </AddButton>
        <ProgressBar active={isProcessing} progress={progress} />
      </ButtonGroup>
    </StyledPlaylistItem>
  );
};