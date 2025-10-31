export interface PlaylistFile {
  name: string;
}

export interface PlaylistProgress {
  status: 'processing' | 'completed' | 'error';
  progress: number;
  message: string;
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
