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
