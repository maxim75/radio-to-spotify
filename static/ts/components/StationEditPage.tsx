import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Station } from '../types';
import {
  PlaylistContainer,
  BatchForm,
  BatchField,
  BatchSelect,
  TextInput,
  FieldHint,
  AddButton,
  ViewButton,
  StatusMessage,
  WarningNote,
} from './styles';

const EMPTY: Station = { station_id: '', playlist_name: '', source: '' };

/**
 * Add or edit one station.
 *
 * Serves both /stations/new and /stations/:stationId - the only difference is whether
 * the form starts empty and whether submitting POSTs or PUTs.
 */
export const StationEditPage: React.FC = () => {
  const { stationId } = useParams<{ stationId: string }>();
  const navigate = useNavigate();
  const isNew = stationId === undefined;

  const [station, setStation] = useState<Station>(EMPTY);
  const [sources, setSources] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // One request serves both jobs: the source list for the dropdown, and the record
    // being edited. There is no per-station endpoint to add for the sake of it.
    const load = async () => {
      try {
        const response = await fetch('/api/stations');
        const data = await response.json();

        if (data.status !== 'success') {
          setError(data.message || 'Could not load the configured stations');
          return;
        }

        setSources(data.sources);

        if (isNew) {
          setStation({ ...EMPTY, source: data.sources[0] ?? '' });
          return;
        }

        const found = (data.stations as Station[]).find((s) => s.station_id === stationId);
        if (found) {
          setStation(found);
        } else {
          setError(`Station ${stationId} is not configured`);
        }
      } catch (e) {
        setError('Network error while loading the station');
      } finally {
        setIsLoading(false);
      }
    };

    load();
  }, [stationId, isNew]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSaving(true);

    try {
      const response = await fetch(
        isNew ? '/api/stations' : `/api/stations/${encodeURIComponent(stationId!)}`,
        {
          method: isNew ? 'POST' : 'PUT',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(station),
        }
      );
      const data = await response.json();

      if (data.status === 'success') {
        navigate('/stations');
      } else {
        // The server does the real validation - it is the side that knows what the
        // rest of the config already contains - so its message is what gets shown.
        setError(data.message || 'Could not save the station');
        setIsSaving(false);
      }
    } catch (e) {
      setError('Network error while saving the station');
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return <PlaylistContainer>Loading...</PlaylistContainer>;
  }

  return (
    <PlaylistContainer>
      <h2>{isNew ? 'Add station' : `Edit ${stationId}`}</h2>

      {!isNew && (
        <WarningNote>
          Changing the <strong>playlist name</strong> does not rename the playlist that
          already exists on Spotify: the next run creates a second one under the new
          name, and the tracks already pushed stay in the old one.
          <br />
          Changing the <strong>station id</strong> leaves the files already scraped
          under the old id in S3, where nothing will pick them up again.
        </WarningNote>
      )}

      {error && <StatusMessage type="error">{error}</StatusMessage>}

      <BatchForm onSubmit={handleSubmit}>
        <BatchField>
          Station id
          <TextInput
            type="text"
            value={station.station_id}
            onChange={(e) => setStation({ ...station, station_id: e.target.value })}
            disabled={isSaving}
            placeholder="16134"
          />
          <FieldHint>
            The Radoxo numeric id or the radiotut slug. Letters, digits, - and _ only.
          </FieldHint>
        </BatchField>

        <BatchField>
          Spotify playlist name
          <TextInput
            type="text"
            value={station.playlist_name}
            onChange={(e) => setStation({ ...station, playlist_name: e.target.value })}
            disabled={isSaving}
            placeholder="Retro FM"
          />
          <FieldHint>Matched by exact name, and created if it does not exist yet.</FieldHint>
        </BatchField>

        <BatchField>
          Source
          <BatchSelect
            value={station.source}
            onChange={(e) => setStation({ ...station, source: e.target.value })}
            disabled={isSaving}
          >
            {sources.map((source) => (
              <option key={source} value={source}>
                {source}
              </option>
            ))}
          </BatchSelect>
          <FieldHint>Which site the playlist is scraped from.</FieldHint>
        </BatchField>

        <AddButton type="submit" disabled={isSaving}>
          {isSaving ? 'Saving...' : 'Save'}
        </AddButton>
        <Link to="/stations" style={{ textDecoration: 'none' }}>
          <ViewButton as="span">Cancel</ViewButton>
        </Link>
      </BatchForm>
    </PlaylistContainer>
  );
};
