import styled from 'styled-components';

export const PlaylistContainer = styled.div`
  max-width: 800px;
  margin: 0 auto;
  padding: 20px;
  font-family: Arial, sans-serif;
`;

export const PlaylistList = styled.ul`
  list-style: none;
  padding: 0;
`;

export const FilterRow = styled.div`
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 15px;
`;

export const FilterInput = styled.input`
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 20px;
  font-size: 14px;
  font-family: inherit;

  &:focus {
    outline: none;
    border-color: #1DB954;
  }
`;

export const FilterCount = styled.span`
  font-size: 14px;
  color: #666;
  white-space: nowrap;
`;

export const PlaylistItem = styled.li`
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px;
  border-bottom: 1px solid #eee;
`;

export const PlaylistName = styled.span`
  font-size: 16px;
  color: #333;
`;

export const ButtonGroup = styled.div`
  display: flex;
  gap: 10px;
  align-items: center;
`;

export const ViewButton = styled.a`
  background-color: #333;
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 20px;
  cursor: pointer;
  font-size: 14px;
  text-decoration: none;
  display: inline-block;

  &:hover {
    background-color: #444;
  }
`;

export const AddButton = styled.button<{ disabled?: boolean }>`
  background-color: ${props => props.disabled ? '#ccc' : '#1DB954'};
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 20px;
  cursor: ${props => props.disabled ? 'not-allowed' : 'pointer'};
  font-size: 14px;
  transition: all 0.3s ease;

  &:hover {
    background-color: ${props => props.disabled ? '#ccc' : '#1ed760'};
  }
`;

export const Progress = styled.div<{ active: boolean }>`
  display: ${props => props.active ? 'flex' : 'none'};
  flex-direction: column;
  gap: 8px;
  color: #666;
  font-size: 14px;
  min-width: 200px;
`;

export const ProgressStatus = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
`;

export const ProgressSpinner = styled.div`
  width: 16px;
  height: 16px;
  border: 2px solid #f3f3f3;
  border-top: 2px solid #1DB954;
  border-radius: 50%;
  animation: spin 1s linear infinite;

  @keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }
`;

export const ProgressBarContainer = styled.div`
  width: 100%;
  height: 4px;
  background-color: #f3f3f3;
  border-radius: 2px;
  overflow: hidden;
`;

export const StyledProgressBar = styled.div<{ width: number }>`
  width: ${props => props.width}%;
  height: 100%;
  background-color: #1DB954;
  transition: width 0.3s ease;
`;

export const ProgressBar = styled.div<{ progress: number }>`
  width: ${props => props.progress}%;
  height: 100%;
  background-color: #1DB954;
  transition: width 0.3s ease;
`;

export const ProgressPercentage = styled.div`
  font-size: 12px;
  color: #666;
  margin-top: 4px;
`;

export const StatusMessage = styled.div<{ type: 'success' | 'error' }>`
  padding: 10px;
  margin: 10px 0;
  border-radius: 4px;
  background-color: ${props => props.type === 'success' ? '#dff0d8' : '#f2dede'};
  color: ${props => props.type === 'success' ? '#3c763d' : '#a94442'};
  border: 1px solid ${props => props.type === 'success' ? '#d6e9c6' : '#ebccd1'};
`;

export const MergeButton = styled.button<{ disabled?: boolean }>`
  background-color: ${props => props.disabled ? '#ccc' : '#ff6b35'};
  color: white;
  border: none;
  padding: 8px 16px;
  border-radius: 20px;
  cursor: ${props => props.disabled ? 'not-allowed' : 'pointer'};
  font-size: 14px;
  transition: all 0.3s ease;
  margin-left: 10px;

  &:hover {
    background-color: ${props => props.disabled ? '#ccc' : '#ff5722'};
  }
`;

export const DropdownContainer = styled.div`
  position: relative;
  display: inline-block;
`;

export const DropdownMenu = styled.div<{ isOpen: boolean }>`
  position: absolute;
  top: 100%;
  left: 0;
  background-color: white;
  border: 1px solid #ddd;
  border-radius: 4px;
  box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
  z-index: 1000;
  min-width: 250px;
  max-height: 300px;
  overflow-y: auto;
  display: ${props => props.isOpen ? 'block' : 'none'};
  margin-top: 5px;
`;

export const DropdownItem = styled.div`
  padding: 10px 15px;
  cursor: pointer;
  border-bottom: 1px solid #eee;
  font-size: 14px;
  
  &:last-child {
    border-bottom: none;
  }
  
  &:hover {
    background-color: #f5f5f5;
  }
  
  .playlist-name {
    font-weight: bold;
    color: #333;
  }
  
  .playlist-details {
    font-size: 12px;
    color: #666;
    margin-top: 2px;
  }
`;

export const PlaylistActions = styled.div`
  display: flex;
  align-items: center;
  gap: 10px;
`;

// Shown wherever a route reports the session has no Spotify token. The routes return
// the URL to use as `auth_url` alongside their 401, so the link is never hardcoded.
// Opens in a new tab: /callback renders a plain "you can close this window" page, so
// navigating the app away from itself would strand the user on that dead end.
export const ConnectSpotifyLink = styled.a`
  background-color: #1DB954;
  color: white;
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 14px;
  font-weight: bold;
  text-decoration: none;
  display: inline-block;

  &:hover {
    background-color: #1ed760;
  }
`;

export const BatchForm = styled.form`
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-end;
  padding: 16px;
  margin-bottom: 20px;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: #fafafa;
`;

export const BatchField = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  color: #555;
`;

export const BatchSelect = styled.select`
  padding: 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: 14px;
  min-width: 180px;
`;

export const BatchDateInput = styled.input`
  padding: 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: 14px;
`;

export const UnmatchedList = styled.ul`
  margin: 8px 0 0;
  padding-left: 20px;
  max-height: 200px;
  overflow-y: auto;
  font-size: 13px;
  color: #666;
`;

export const TextInput = styled.input`
  padding: 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: 14px;
  min-width: 220px;
  font-family: inherit;

  &:focus {
    outline: none;
    border-color: #1DB954;
  }
`;

export const FieldHint = styled.span`
  font-size: 12px;
  color: #888;
`;

export const DangerButton = styled.button`
  background-color: #fff;
  color: #a94442;
  border: 1px solid #ebccd1;
  padding: 8px 16px;
  border-radius: 20px;
  cursor: pointer;
  font-size: 14px;
  transition: all 0.3s ease;

  &:hover {
    background-color: #f2dede;
  }

  &:disabled {
    color: #ccc;
    border-color: #eee;
    cursor: not-allowed;
    background-color: #fff;
  }
`;

// The station id and source under a station's playlist name in the list.
export const StationMeta = styled.span`
  display: block;
  font-size: 12px;
  color: #666;
  margin-top: 2px;
`;

// The two irreversible-ish consequences of an edit, stated where the edit is made.
export const WarningNote = styled.div`
  padding: 10px 12px;
  margin: 10px 0 20px;
  border: 1px solid #faebcc;
  border-radius: 4px;
  background-color: #fcf8e3;
  color: #8a6d3b;
  font-size: 13px;
  line-height: 1.5;
`;
