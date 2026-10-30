import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api, type PlanResource } from '../../api/client';
import { PlanCreateForm } from './PlanCreateForm';

vi.mock('../../api/client', () => ({
  api: {
    config: vi.fn().mockResolvedValue({
      default_effort: 'medium', codex_effort_options: ['low', 'medium', 'high', 'xhigh'],
      codex_model_efforts: { 'gpt-6.1-sol': ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'] },
      codex_model_service_tiers: { 'gpt-6.1-sol': ['default', 'priority'] },
      plan_pipeline_defaults: {
        planner: { primary: { provider: 'codex', model: 'gpt-6.1-sol', effort: 'medium' } },
        reviewer: { enabled: true, primary: { provider: 'codex', model: 'gpt-6.1-sol', effort: 'medium' } },
      },
    }),
    listProjects: vi.fn(),
    listTags: vi.fn(),
    listWorkers: vi.fn(),
    createPlan: vi.fn(),
    createProject: vi.fn(),
  },
}));

const uploadState = {
  uploads: [],
  uploadedResults: [],
  isUploading: false,
  hasFailed: false,
  addFiles: vi.fn(),
  removeFile: vi.fn(),
  clear: vi.fn(),
};
vi.mock('../../hooks/useFileUpload', () => ({
  useFileUpload: () => uploadState,
}));
vi.mock('../../hooks/useFileDrop', () => ({
  useFileDrop: vi.fn(),
}));
vi.mock('../Voice/VoiceButton', () => ({
  VoiceButton: () => null,
}));
vi.mock('../ProjectSelect', () => ({
  ProjectSelect: ({
    value,
    onChange,
  }: {
    value?: number | string;
    onChange: (value: string) => void;
  }) => <select aria-label="Plan project" value={value || ''} onChange={(event) => onChange(event.target.value)}>
    <option value="">Select project</option>
    <option value="3">Repository</option>
  </select>,
}));

const createdPlan = {
  id: 19,
  title: 'Migration design',
} as PlanResource;

describe('PlanCreateForm', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(api.listProjects).mockResolvedValue([{
      id: 3,
      name: 'Repository',
      show_in_selector: true,
      tags: [],
    }] as never);
    vi.mocked(api.listTags).mockResolvedValue([]);
    vi.mocked(api.listWorkers).mockResolvedValue([]);
    vi.mocked(api.createPlan).mockResolvedValue(createdPlan);
  });

  it('creates a canonical standalone Plan without Task routing fields', async () => {
    const onCreated = vi.fn();
    render(<PlanCreateForm onCreated={onCreated} />);

    await userEvent.type(screen.getByPlaceholderText('Plan title (optional)'), 'Migration design');
    await userEvent.type(screen.getByPlaceholderText('What should this Plan investigate and decide?'), 'Compare migration strategies');
    await userEvent.selectOptions(screen.getByLabelText('Plan project'), '3');
    await userEvent.click(screen.getByRole('button', { name: 'Create Plan' }));

    await waitFor(() => expect(api.createPlan).toHaveBeenCalledWith({
      input: 'Compare migration strategies',
      title: 'Migration design',
      project_id: 3,
      priority: 0,
    }));
    const payload = vi.mocked(api.createPlan).mock.calls[0][0];
    expect(payload).not.toHaveProperty('provider');
    expect(payload).not.toHaveProperty('model');
    expect(payload).not.toHaveProperty('pipeline_config');
    expect(onCreated).toHaveBeenCalledWith(createdPlan);
  });
});

 it('freezes speed and effort selections in the Plan creation request', async () => {
  render(<PlanCreateForm onCreated={vi.fn()} />);
  const speed = await screen.findByRole('combobox', { name: 'Planner speed' });
  await waitFor(() => expect(speed).toBeEnabled());
  expect(speed).toHaveValue('default');
  await userEvent.selectOptions(speed, 'priority');
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Planner reasoning effort' }), 'max');
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Reviewer reasoning effort' }), 'high');
  await userEvent.type(screen.getByPlaceholderText('What should this Plan investigate and decide?'), 'Investigate');
  await userEvent.selectOptions(screen.getByLabelText('Plan project'), '3');
  await userEvent.click(screen.getByRole('button', { name: 'Create Plan' }));
  await waitFor(() => expect(api.createPlan).toHaveBeenCalledWith(expect.objectContaining({
    runtime_options: {
      planner: { codex_service_tier: 'priority', effort: 'max' },
      reviewer: { effort: 'high' },
    },
  })));
});
