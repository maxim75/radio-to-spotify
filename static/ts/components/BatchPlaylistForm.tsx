import React, { useEffect, useRef, useState } from 'react';
import { BatchResult, PlaylistProgress } from '../types';
import { ProgressBar } from './ProgressBar';
import {
  BatchForm,
  BatchField,
  BatchSelect,
  BatchDateInput,
  AddButton,
  ConnectSpotifyLink,
  StatusMessage,
  UnmatchedList,
} from './styles';

const today = () => new Date().toISOString().slice(0, 10);

export const BatchPlaylistForm: React.FC = () => {
  const [stations, setStations] = useState<Record<string, string>>({});
  const [stationId, setStationId] = useState<string>('');
  const [startDate, setStartDate] = useState<string>(today());
  const [endDate, setEndDate] = useState<string>(today());
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set from the `auth_url` the server returns, so the control that failed is also
  // where the user can fix it - same pattern as PlaylistItem.
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [result, setResult] = useState<BatchResult | null>(null);
  const [progress, setProgress] = useState<PlaylistProgress>({
    status: 'processing',
    progress: 0,
    message: 'Initializing...',
  });
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    fetch('/api/station-playlists')
      .then((response) => response.json())
      .then((data) => {
        if (data.status === 'success') {
          setStations(data.stations);
          const ids = Object.keys(data.stations);
          if (ids.length > 0) {
            setStationId(ids[0]);
          }
        }
      })
      .catch((e) => console.error('Error fetching station playlists:', e));

    // Clear the interval on unmount, or it keeps polling a task nobody is watching.
    return () => {
      if (pollRef.current !== null) {
        window.clearInterval(pollRef.current);
      }
    };
  }, []);

  const pollProgress = (taskId: string) => {
    pollRef.current = window.setInterval(async () => {
      try {
        const response = await fetch(`/playlist_progress/${taskId}`);
        const data = await response.json();

        setProgress({
          status: data.status,
          progress: data.progress,
          message: data.message,
        });

        if (data.status === 'completed' || data.status === 'error') {
          if (pollRef.current !== null) {
            window.clearInterval(pollRef.current);
            pollRef.current = null;
          }
          setIsRunning(false);
          setResult(data.result ?? null);
        }
      } catch (e) {
        if (pollRef.current !== null) {
          window.clearInterval(pollRef.current);
          pollRef.current = null;
        }
        setIsRunning(false);
        setProgress({
          status: 'error',
          progress: 0,
          message: 'Lost contact with the server while running the batch',
        });
      }
    }, 1000);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setAuthUrl(null);
    setResult(null);
    setIsRunning(true);
    setProgress({ status: 'processing', progress: 0, message: 'Starting batch...' });

    try {
      const response = await fetch('/create-playlist-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          station_id: stationId,
          start_date: startDate,
          end_date: endDate,
        }),
      });
      const data = await response.json();

      if (data.status === 'success') {
        pollProgress(data.task_id);
      } else {
        setIsRunning(false);
        setError(data.message || 'Could not start the batch');
        setAuthUrl(data.auth_url || null);
      }
    } catch (e) {
      setIsRunning(false);
      setError('Network error while starting the batch');
    }
  };

  const stationIds = Object.keys(stations);

  if (stationIds.length === 0) {
    return null;
  }

  return (
    <div>
      <BatchForm onSubmit={handleSubmit}>
        <BatchField>
          Station
          <BatchSelect
            value={stationId}
            onChange={(e) => setStationId(e.target.value)}
            disabled={isRunning}
          >
            {stationIds.map((id) => (
              <option key={id} value={id}>
                {stations[id]} ({id})
              </option>
            ))}
          </BatchSelect>
        </BatchField>
        <BatchField>
          From
          <BatchDateInput
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            disabled={isRunning}
          />
        </BatchField>
        <BatchField>
          To
          <BatchDateInput
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            disabled={isRunning}
          />
        </BatchField>
        <AddButton type="submit" disabled={isRunning}>
          {isRunning ? 'Running...' : 'Add to Spotify playlist'}
        </AddButton>
      </BatchForm>

      <ProgressBar active={isRunning || progress.status !== 'processing'} progress={progress} />

      {error && (
        <StatusMessage type="error">
          {error}
          {authUrl && <ConnectSpotifyLink href={authUrl}>Connect Spotify</ConnectSpotifyLink>}
        </StatusMessage>
      )}

      {result && (
        <StatusMessage type="success">
          Added {result.added} track{result.added === 1 ? '' : 's'} to "{result.playlist_name}"
          from {result.files} file{result.files === 1 ? '' : 's'} —{' '}
          {result.skipped_existing} already present, {result.unmatched.length} not found on Spotify.
          {result.unmatched.length > 0 && (
            <UnmatchedList>
              {result.unmatched.map((track, index) => (
                <li key={`${track.artist}-${track.song}-${index}`}>
                  {track.artist} — {track.song}
                </li>
              ))}
            </UnmatchedList>
          )}
        </StatusMessage>
      )}
    </div>
  );
};
