import React from 'react';
import { PlaylistProgress } from '../types';
import {
  Progress,
  ProgressStatus,
  ProgressSpinner,
  ProgressBarContainer,
  StyledProgressBar,
  ProgressPercentage
} from './styles';

interface ProgressBarProps {
  active: boolean;
  progress: PlaylistProgress;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ active, progress }) => (
  <Progress active={active}>
    <ProgressStatus>
      <ProgressSpinner />
      <span>{progress.message}</span>
    </ProgressStatus>
    <ProgressBarContainer>
      <StyledProgressBar width={progress.progress} />
    </ProgressBarContainer>
    <ProgressPercentage>{progress.progress}%</ProgressPercentage>
  </Progress>
);