import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SystemMetricsPanel } from './SystemMetricsPanel';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: { systemMetrics: vi.fn() },
}));

const snapshot = {
  collected_at: '2026-09-01T12:00:00+00:00',
  cached: true,
  cache_age_seconds: 120,
  cpu: { utilization_percent: 12.5, load_1m: 0.2, cores: 4 },
  memory: {
    total_bytes: 1000, used_bytes: 400, available_bytes: 600, used_percent: 40,
    swap_total_bytes: 2000, swap_used_bytes: 100, swap_used_percent: 5,
  },
  storage: {
    root: { path: '/', total_bytes: 1000, used_bytes: 250, free_bytes: 750, used_percent: 25 },
    tmp: { path: '/tmp', total_bytes: 1000, used_bytes: 50, free_bytes: 950, used_percent: 5 },
  },
};

describe('SystemMetricsPanel', () => {
  it('loads a cached snapshot and manually requests a fresh one', async () => {
    vi.mocked(api.systemMetrics).mockResolvedValue(snapshot);
    render(<SystemMetricsPanel />);

    expect(await screen.findByText('12.5%')).toBeInTheDocument();
    expect(api.systemMetrics).toHaveBeenCalledWith(false);
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));
    await waitFor(() => expect(api.systemMetrics).toHaveBeenLastCalledWith(true));
  });
});
