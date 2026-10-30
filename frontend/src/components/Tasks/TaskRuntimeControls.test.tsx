import { render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, type Task } from '../../api/client';
import { TaskRuntimeControls } from './TaskRuntimeControls';

vi.mock('../../api/client', () => ({ api: { config: vi.fn(), updateTask: vi.fn() } }));
const task = { id: 7, provider: 'codex', mode: 'auto', model: 'gpt-6.1-sol',
  effort_level: 'medium', codex_service_tier: 'default', status: 'completed',
  shared_from_id: null, background_active: false } as Task;
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.config).mockResolvedValue({ default_effort: 'medium',
    codex_effort_options: ['low', 'medium', 'high', 'xhigh'],
    codex_model_efforts: { 'gpt-6.1-sol': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'] },
    codex_model_service_tiers: { 'gpt-6.1-sol': ['default', 'priority'] },
  } as never);
});
afterEach(cleanup);

describe('visible task/chat runtime controls', () => {
  it('defaults to Standard and persists Fast and reasoning independently', async () => {
    const onUpdated = vi.fn();
    const { rerender } = render(<TaskRuntimeControls task={task} onUpdated={onUpdated} />);
    const speed = await screen.findByRole('combobox', { name: 'Codex speed' });
    await waitFor(() => expect(speed).toBeEnabled());
    expect(speed).toHaveValue('default');
    const fast = { ...task, codex_service_tier: 'priority' as const };
    vi.mocked(api.updateTask).mockResolvedValue(fast);
    await userEvent.selectOptions(speed, 'priority');
    await waitFor(() => expect(api.updateTask).toHaveBeenCalledWith(7, { codex_service_tier: 'priority' }));
    expect(onUpdated).toHaveBeenCalledWith(fast);
    rerender(<TaskRuntimeControls task={fast} onUpdated={onUpdated} />);
    vi.mocked(api.updateTask).mockResolvedValue({ ...fast, effort_level: 'max' });
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Reasoning effort' }), 'max');
    await waitFor(() => expect(api.updateTask).toHaveBeenLastCalledWith(7, { effort_level: 'max' }));
  });

  it('retains Standard and displays an error if saving Fast fails', async () => {
    vi.mocked(api.updateTask).mockRejectedValue(new Error('route changed'));
    render(<TaskRuntimeControls task={task} onUpdated={vi.fn()} />);
    const speed = screen.getByRole('combobox', { name: 'Codex speed' });
    await waitFor(() => expect(speed).toBeEnabled());
    await userEvent.selectOptions(speed, 'priority');
    expect(await screen.findByRole('alert')).toHaveTextContent('route changed');
    expect(speed).toHaveValue('default');
  });

  it('does not change routing during execution or on shared tasks', async () => {
    render(<TaskRuntimeControls task={{ ...task, status: 'executing' }} onUpdated={vi.fn()} />);
    await waitFor(() => expect(api.config).toHaveBeenCalled());
    expect(screen.getByRole('combobox', { name: 'Codex speed' })).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Reasoning effort' })).toBeDisabled();
    expect(api.updateTask).not.toHaveBeenCalled();
  });
});
