import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Station } from '../types';
import {
  PlaylistContainer,
  PlaylistList,
  PlaylistItem,
  PlaylistName,
  ButtonGroup,
  ViewButton,
  AddButton,
  DangerButton,
  StatusMessage,
  StationMeta,
} from './styles';

/**
 * The configured radio stations.
 *
 * This list is the whole of station_playlists.json, which drives both what the
 * nightly job scrapes and which Spotify playlist each station's tracks land in - so
 * removing a station here stops both, and adding one starts both.
 */
export const StationsPage: React.FC = () => {
  const [stations, setStations] = useState<Station[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStations = async () => {
    try {
      const response = await fetch('/api/stations');
      const data = await response.json();

      if (data.status === 'success') {
        setStations(data.stations);
        setError(null);
      } else {
        setError(data.message || 'Could not load the configured stations');
      }
    } catch (e) {
      setError('Network error while loading the configured stations');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchStations();
  }, []);

  const handleDelete = async (station: Station) => {
    const confirmed = window.confirm(
      `Remove ${station.station_id}? It stops being scraped and stops being added to ` +
      `"${station.playlist_name}". The Spotify playlist and the files already in S3 are left alone.`
    );
    if (!confirmed) {
      return;
    }

    try {
      const response = await fetch(`/api/stations/${encodeURIComponent(station.station_id)}`, {
        method: 'DELETE',
        headers: { Accept: 'application/json' },
      });
      const data = await response.json();

      if (data.status === 'success') {
        // Refetch rather than splice the local copy: the file is the source of truth
        // and another tab may have changed it.
        fetchStations();
      } else {
        setError(data.message || 'Could not remove the station');
      }
    } catch (e) {
      setError('Network error while removing the station');
    }
  };

  if (isLoading) {
    return <PlaylistContainer>Loading stations...</PlaylistContainer>;
  }

  return (
    <PlaylistContainer>
      <h2>Radio stations</h2>
      <p style={{ color: '#666', fontSize: '14px' }}>
        Each station is scraped nightly and its tracks are added to the Spotify playlist
        named here.
      </p>

      {error && <StatusMessage type="error">{error}</StatusMessage>}

      <div style={{ margin: '15px 0' }}>
        <Link to="/stations/new" style={{ textDecoration: 'none' }}>
          {/* as="span": a <button> inside an <a> is invalid HTML and swallows the
              link's click on some browsers. */}
          <AddButton as="span">Add station</AddButton>
        </Link>
      </div>

      {stations.length === 0 ? (
        <p style={{ color: '#666' }}>
          No stations are configured, so nothing is scraped and nothing is pushed to
          Spotify. Add one to start.
        </p>
      ) : (
        <PlaylistList>
          {stations.map((station) => (
            <PlaylistItem key={station.station_id}>
              <PlaylistName>
                {station.playlist_name}
                <StationMeta>
                  {station.station_id} · {station.source}
                </StationMeta>
              </PlaylistName>
              <ButtonGroup>
                <Link
                  to={`/stations/${encodeURIComponent(station.station_id)}`}
                  style={{ textDecoration: 'none' }}
                >
                  <ViewButton as="span">Edit</ViewButton>
                </Link>
                <DangerButton type="button" onClick={() => handleDelete(station)}>
                  Delete
                </DangerButton>
              </ButtonGroup>
            </PlaylistItem>
          ))}
        </PlaylistList>
      )}
    </PlaylistContainer>
  );
};
