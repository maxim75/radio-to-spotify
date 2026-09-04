export interface PlaylistFile {
  name: string;
}

export interface BatchResult {
  playlist_id?: string;
  playlist_name: string;
  added: number;
  skipped_existing: number;
  unmatched: Array<{ artist: string; song: string }>;
  files: number;
}

export interface PlaylistProgress {
  status: 'processing' | 'completed' | 'error';
  progress: number;
  message: string;
  // Only batch runs report one; the create/merge jobs leave it null.
  result?: BatchResult | null;
}

export interface SpotifyPlaylist {
  id: string;
  name: string;
  description: string;
  public: boolean;
  collaborative: boolean;
  tracks_total: number;
  owner: string;
  owner_id: string;
  href: string;
  external_url: string;
  images: Array<{url: string; height: number | null; width: number | null}>;
  snapshot_id: string;
}

export interface Task {
  taskId: string;
  status: string;
  message: string;
}

export interface SpotifyTrack {
  id: string;
  name: string;
  artist: string;
  uri: string;
  album: string;
}

export interface MergeProgress {
  status: 'processing' | 'completed' | 'error';
  progress: number;
  message: string;
}

export interface Station {
  station_id: string;
  playlist_name: string;
  // Which scraper feeds this station. The edit form fills its dropdown from
  // /api/stations rather than hardcoding the values a second time.
  source: string;
}
