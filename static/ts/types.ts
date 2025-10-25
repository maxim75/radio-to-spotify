export interface PlaylistFile {
  name: string;
}

export interface PlaylistProgress {
  status: 'processing' | 'completed' | 'error';
  progress: number;
  message: string;
}

export interface Task {
  taskId: string;
  status: string;
  message: string;
}