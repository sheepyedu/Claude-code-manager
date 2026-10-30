import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TasksPage } from './TasksPage';
import { api, type Task } from '../api/client';

let capturedGlobalWs:
  | ((message: Record<string, unknown>) => void)
  | undefined;
const taskSearchMock = vi.hoisted(() => ({
  initialResults: null as Array<Record<string, unknown>> | null,
}));

vi.mock('../api/client', () => ({
  api: {
    getRuntimeSettings: vi.fn().mockResolvedValue({
      auto_sort_on_access: true,
    }),
    listTasks: vi.fn(),
    countTasks: vi.fn(),
    listProjects: vi.fn(),
    listTags: vi.fn(),
    getTask: vi.fn(),
    markTaskRead: vi.fn().mockResolvedValue({}),
    starTask: vi.fn().mockResolvedValue({}),
    archiveTask: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn((
    _channels: string[],
    onMessage?: (message: Record<string, unknown>) => void,
  ) => {
    capturedGlobalWs = onMessage;
    return { lastMessage: null, isConnected: true };
  }),
}));

vi.mock('../hooks/useTaskSearch', async () => {
  const React = await import('react');
  return {
    useTaskSearch: () => React.useState(taskSearchMock.initialResults),
  };
});

vi.mock('../hooks/useTaskReorder', () => ({
  mergeVisibleTaskOrder: (
    _current: Record<string, unknown>[],
    optimistic: Record<string, unknown>[],
  ) => optimistic,
  useTaskReorder: () => ({
    itemProps: () => ({ draggable: true, 'data-reorder-source': 'true' }),
    dropTargetProps: () => ({ 'data-reorder-target': 'true' }),
    targetProps: () => ({ 'data-reorder-target': 'true' }),
    draggingId: null,
    overIndex: null,
  }),
}));

vi.mock('../components/Tasks/TaskForm', () => ({
  TaskForm: () => null,
}));
vi.mock('../components/Tasks/TaskList', () => ({
  TaskList: ({
    tasks,
    onTaskUpdated,
  }: {
    tasks: Task[];
    onTaskUpdated?: (task: Task) => void;
  }) => (
    <div data-testid="task-snapshots">
      {tasks.map((task) => (
        <div key={task.id}>
          <span>
            {task.id}:{task.status}:{String(task.background_active === true)}
          </span>
          <span data-testid={`plan-stage-${task.id}`}>
            {task.plan_stage || 'none'}:{task.plan_stage_round ?? 'none'}
          </span>
          <span data-testid={`plan-route-${task.id}`}>
            {task.plan_stage_provider || 'none'}:{task.plan_stage_model || 'none'}:{task.plan_stage_route_slot || 'none'}
          </span>
          <span data-testid={`runtime-${task.id}`}>{task.effort_level}:{task.codex_service_tier}</span>
          <span data-testid={`attention-tag-${task.id}`}>
            {task.attention_tag ?? ''}
          </span>
          <span data-testid={`sub-agents-badge-${task.id}`}>
            Sub-agents:{task.active_sub_agents ?? 0}
          </span>
        </div>
      ))}
      {tasks[0] && onTaskUpdated && (
        <>
          <button onClick={() => onTaskUpdated({ ...tasks[0], effort_level: 'max', codex_service_tier: 'priority', status: 'pending', background_active: false })}>Simulate runtime update</button>
          <button onClick={() => onTaskUpdated({ ...tasks[0], effort_level: 'ultra', codex_service_tier: 'priority', turn_generation: -1 })}>Simulate stale runtime update</button>
          <button onClick={() => onTaskUpdated({ ...tasks[0], attention_tag: '新增标签' })}>
            Simulate add attention tag
          </button>
          <button onClick={() => onTaskUpdated({ ...tasks[0], attention_tag: '修改标签' })}>
            Simulate edit attention tag
          </button>
          <button onClick={() => onTaskUpdated({ ...tasks[0], attention_tag: null })}>
            Simulate clear attention tag
          </button>
        </>
      )}
    </div>
  ),
}));
vi.mock('../components/Chat/ChatView', () => ({
  ChatView: () => null,
}));
vi.mock('../components/Chat/LoopChatView', () => ({
  LoopChatView: () => null,
}));
vi.mock('../components/ProjectSelect', () => ({
  ProjectSelect: ({ onChange }: { onChange: (value: string) => void }) => (
    <>
      <button type="button">Projects</button>
      <button type="button" onClick={() => onChange('42')}>Select project 42</button>
      <button type="button" onClick={() => onChange('43')}>Select project 43</button>
    </>
  ),
}));
vi.mock('../components/Tasks/TaskBadges', () => ({
  PluginsBadge: ({ task }: { task: { id: number } }) => (
    <span data-testid={`plugins-badge-${task.id}`}>Plugins</span>
  ),
  SubAgentsBadge: ({ task }: { task: { id: number; active_sub_agents: number } }) => (
    <span data-testid={`sub-agents-badge-${task.id}`}>
      Sub-agents:{task.active_sub_agents}
    </span>
  ),
}));
vi.mock('../components/TeamShareModal', () => ({
  TeamShareModal: () => null,
}));

const task = {
  id: 7,
  title: 'Realtime task',
  description: 'd',
  status: 'pending',
  background_active: false,
  active_sub_agents: 0,
  priority: 0,
  project_id: null,
  starred: false,
  archived: false,
  has_unread: false,
  mode: 'auto',
};

describe('TasksPage realtime reconciliation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedGlobalWs = undefined;
    taskSearchMock.initialResults = null;
    localStorage.clear();
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 1024,
    });
    vi.mocked(api.listTasks).mockResolvedValue([task] as never);
    vi.mocked(api.countTasks).mockResolvedValue({ total: 1 });
    vi.mocked(api.listProjects).mockResolvedValue([]);
    vi.mocked(api.listTags).mockResolvedValue([]);
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn().mockReturnValue({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }),
    });
  });

  it('refreshes counts/filter membership after status_change but not a marker-only event', async () => {
    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(api.countTasks).toHaveBeenCalledTimes(1);
      expect(capturedGlobalWs).toBeTypeOf('function');
    });

    act(() => {
      capturedGlobalWs?.({
        channel: 'tasks',
        data: {
          event: 'status_change',
          task_id: 7,
          new_status: 'completed',
          background_active: false,
        },
      });
    });
    await waitFor(() => {
      expect(api.countTasks).toHaveBeenCalledTimes(2);
    });
    expect(api.listProjects).toHaveBeenCalledTimes(1);
    expect(api.listTags).toHaveBeenCalledTimes(1);

    act(() => {
      capturedGlobalWs?.({
        channel: 'tasks',
        data: {
          event: 'background_activity',
          task_id: 7,
          background_active: true,
        },
      });
    });

    expect(await screen.findByText('7:pending:true')).toBeInTheDocument();
    await act(async () => {
      await Promise.resolve();
    });
    expect(api.countTasks).toHaveBeenCalledTimes(2);
  });

  it('switches projects from page two with one page-zero request and no catalog reload', async () => {
    vi.mocked(api.countTasks).mockResolvedValue({ total: 41 });

    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );

    await screen.findByText('1 / 3');
    const pagination = screen.getByText('(41 tasks)').parentElement?.parentElement;
    expect(pagination).not.toBeNull();
    const paginationButtons = within(pagination as HTMLElement).getAllByRole('button');
    await userEvent.click(paginationButtons[1]);
    await screen.findByText('2 / 3');

    vi.mocked(api.listTasks).mockClear();
    vi.mocked(api.countTasks).mockClear();
    await userEvent.click(screen.getByRole('button', { name: 'Select project 42' }));

    await waitFor(() => {
      expect(api.listTasks).toHaveBeenCalledWith(
        undefined,
        false,
        42,
        undefined,
        20,
        0,
        false,
        undefined,
      );
    });
    expect(vi.mocked(api.listTasks).mock.calls.filter((call) => call[2] === 42)).toHaveLength(1);
    expect(api.countTasks).toHaveBeenCalledTimes(1);
    expect(api.listProjects).toHaveBeenCalledTimes(1);
    expect(api.listTags).toHaveBeenCalledTimes(1);
  });

  it('ignores a slower response from the previously selected project', async () => {
    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );
    await screen.findByText('7:pending:false');

    let resolveProject42!: (tasks: typeof task[]) => void;
    let resolveProject43!: (tasks: typeof task[]) => void;
    const project42 = new Promise<typeof task[]>((resolve) => { resolveProject42 = resolve; });
    const project43 = new Promise<typeof task[]>((resolve) => { resolveProject43 = resolve; });
    vi.mocked(api.listTasks).mockImplementation((
      _status,
      _includeArchived,
      projectId,
    ) => {
      if (projectId === 42) return project42 as never;
      if (projectId === 43) return project43 as never;
      return Promise.resolve([task]) as never;
    });

    await userEvent.click(screen.getByRole('button', { name: 'Select project 42' }));
    await waitFor(() => expect(vi.mocked(api.listTasks).mock.calls.some((call) => call[2] === 42)).toBe(true));
    await userEvent.click(screen.getByRole('button', { name: 'Select project 43' }));
    await waitFor(() => expect(vi.mocked(api.listTasks).mock.calls.some((call) => call[2] === 43)).toBe(true));

    await act(async () => {
      resolveProject43([{ ...task, id: 43, project_id: 43 }]);
    });
    expect(await screen.findByText('43:pending:false')).toBeInTheDocument();

    await act(async () => {
      resolveProject42([{ ...task, id: 42, project_id: 42 }]);
    });
    expect(screen.getByText('43:pending:false')).toBeInTheDocument();
    expect(screen.queryByText('42:pending:false')).not.toBeInTheDocument();
  });

  it('applies Plan stage changes without waiting for polling', async () => {
    vi.mocked(api.listTasks).mockResolvedValue([{
      ...task,
      mode: 'plan',
      status: 'executing',
      plan_stage: 'planning',
      plan_stage_round: 1,
    }] as never);

    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );

    expect(await screen.findByTestId('plan-stage-7')).toHaveTextContent(
      'planning:1',
    );
    const countCalls = vi.mocked(api.countTasks).mock.calls.length;

    act(() => {
      capturedGlobalWs?.({
        channel: 'tasks',
        data: {
          event: 'plan_stage_change',
          task_id: 7,
          plan_stage: 'reviewing',
          plan_stage_round: 2,
          plan_stage_provider: 'codex',
          plan_stage_model: 'gpt-5.6-terra',
          plan_stage_effort: 'xhigh',
          plan_stage_route_slot: 'fallback',
        },
      });
    });

    expect(await screen.findByTestId('plan-stage-7')).toHaveTextContent(
      'reviewing:2',
    );
    expect(await screen.findByTestId('plan-route-7')).toHaveTextContent(
      'codex:gpt-5.6-terra:fallback',
    );
    expect(api.countTasks).toHaveBeenCalledTimes(countCalls);

    act(() => {
      capturedGlobalWs?.({
        channel: 'tasks',
        data: {
          event: 'plan_ready',
          task_id: 7,
        },
      });
    });

    expect(await screen.findByText('7:plan_review:false')).toBeInTheDocument();
    expect(api.countTasks).toHaveBeenCalledTimes(countCalls);
  });

  it('applies native sub-agent counts without waiting for polling', async () => {
    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );

    expect(await screen.findByTestId('sub-agents-badge-7')).toHaveTextContent(
      'Sub-agents:0',
    );
    const countCalls = vi.mocked(api.countTasks).mock.calls.length;

    act(() => {
      capturedGlobalWs?.({
        channel: 'tasks',
        data: {
          event: 'sub_agent_count',
          task_id: 7,
          active_sub_agents: 3,
        },
      });
    });

    expect(await screen.findByTestId('sub-agents-badge-7')).toHaveTextContent(
      'Sub-agents:3',
    );
    expect(api.countTasks).toHaveBeenCalledTimes(countCalls);
  });

  it('queries the complete Task history without embedding Plan catalog panels', async () => {
    vi.mocked(api.listTasks).mockResolvedValue([{ ...task, id: 1 }] as never);
    vi.mocked(api.countTasks).mockResolvedValue({ total: 1 });

    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );

    expect(await screen.findByText('1:pending:false')).toBeInTheDocument();
    await waitFor(() => expect(api.listTasks).toHaveBeenCalled());
    expect(vi.mocked(api.listTasks).mock.calls.every((call) => call[8] === undefined)).toBe(true);
    expect(vi.mocked(api.countTasks).mock.calls.every((call) => call[6] === undefined)).toBe(true);
    expect(screen.queryByRole('region', { name: 'Plans requiring action' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Filter/ }));
    expect(screen.queryByText('Standalone Plans')).not.toBeInTheDocument();
    expect(screen.queryByText('Related Plans')).not.toBeInTheDocument();
  });

  it('reconciles saved runtime controls without overwriting newer WS lifecycle fields', async () => {
    vi.mocked(api.listTasks).mockResolvedValue([{ ...task, provider: 'codex', model: 'gpt-6.1-sol', retry_count: 0, turn_generation: 2, effort_level: 'medium', codex_service_tier: 'default' }] as never);
    render(<TasksPage chatTaskId={null} onChatTaskChange={vi.fn()} />);
    const runtime = await screen.findByTestId('runtime-7');
    expect(runtime).toHaveTextContent('medium:default');
    act(() => capturedGlobalWs?.({ channel: 'tasks', data: { event: 'background_activity', task_id: 7, background_active: true } }));
    await userEvent.click(screen.getByText('Simulate runtime update'));
    expect(runtime).toHaveTextContent('max:priority');
    expect(screen.getByText('7:pending:true')).toBeInTheDocument();
  });

  it('does not apply a runtime response from an older turn generation', async () => {
    vi.mocked(api.listTasks).mockResolvedValue([{ ...task, provider: 'codex', model: 'gpt-6.1-sol', retry_count: 0, turn_generation: 2, effort_level: 'medium', codex_service_tier: 'default' }] as never);
    render(<TasksPage chatTaskId={null} onChatTaskChange={vi.fn()} />);
    const runtime = await screen.findByTestId('runtime-7');
    await userEvent.click(screen.getByText('Simulate stale runtime update'));
    expect(runtime).toHaveTextContent('medium:default');
  });

  it('places search directly after Filter and before Projects', async () => {
    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );

    const filterButton = await screen.findByRole('button', { name: /Filter/ });
    const searchButton = screen.getByTitle('Search task titles (regex)');
    const projectButton = screen.getByRole('button', { name: 'Projects' });

    expect(filterButton.compareDocumentPosition(searchButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(searchButton.compareDocumentPosition(projectButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows the attention tag in the split-mode task sidebar', async () => {
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 1440,
    });
    vi.mocked(api.listTasks).mockResolvedValue([
      { ...task, attention_tag: '等待任务结束' },
    ] as never);

    render(
      <TasksPage
        chatTaskId={task.id}
        onChatTaskChange={vi.fn()}
      />,
    );

    expect(await screen.findByText('等待任务结束')).toBeInTheDocument();
  });

  it('keeps Delivery rows read-only in the split sidebar', async () => {
    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 1440,
    });
    vi.mocked(api.listTasks).mockResolvedValue([{
      ...task,
      mode: 'delivery_loop',
      delivery_run_id: 42,
      delivery_activity: 'waiting',
      status: 'in_progress',
    }] as never);

    render(
      <TasksPage
        chatTaskId={task.id}
        onChatTaskChange={vi.fn()}
      />,
    );

    const row = await screen.findByTestId('task-sidebar-row-7');
    expect(row).toHaveAttribute('data-reorder-target', 'true');
    expect(row).not.toHaveAttribute('data-reorder-source');
    expect(row).not.toHaveAttribute('draggable');
    expect(within(row).queryByTestId('plugins-badge-7')).not.toBeInTheDocument();
    expect(within(row).queryByTitle('Share')).not.toBeInTheDocument();
    expect(within(row).queryByTitle('Archive')).not.toBeInTheDocument();
    expect(within(row).getByTitle('Star')).toBeInTheDocument();
    expect(screen.getByTestId('task-sidebar-status-7')).toHaveClass('bg-indigo-400');
  });

  it('offers the Delivery Waiting filter with its indigo status color', async () => {
    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );

    await waitFor(() => expect(api.countTasks).toHaveBeenCalled());
    await userEvent.click(screen.getByRole('button', { name: /Filter/ }));
    const deliveryFilter = screen.getByRole('button', { name: /Delivery Waiting/ });
    expect(deliveryFilter.querySelector('.bg-indigo-500')).not.toBeNull();
    await userEvent.click(deliveryFilter);

    await waitFor(() => {
      expect(vi.mocked(api.listTasks).mock.calls.some((call) => (
        call[0] === 'delivery_waiting'
      ))).toBe(true);
    });
  });

  it('immediately reconciles add, edit, and clear into active search results', async () => {
    taskSearchMock.initialResults = [
      { ...task, attention_tag: null },
    ];

    render(
      <TasksPage
        chatTaskId={null}
        onChatTaskChange={vi.fn()}
      />,
    );

    const visibleTag = await screen.findByTestId('attention-tag-7');
    expect(visibleTag).toBeEmptyDOMElement();

    await userEvent.click(screen.getByText('Simulate add attention tag'));
    expect(visibleTag).toHaveTextContent('新增标签');

    await userEvent.click(screen.getByText('Simulate edit attention tag'));
    expect(visibleTag).toHaveTextContent('修改标签');

    await userEvent.click(screen.getByText('Simulate clear attention tag'));
    expect(visibleTag).toBeEmptyDOMElement();
  });
});
