import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LanguageSelect } from './LanguageSelect';
import { MarkdownContent } from '../MarkdownContent';
import { InstanceGrid } from '../Instances/InstanceGrid';
import { getLanguage, setLanguage } from '../../config/language';

const api = vi.hoisted(() => ({ dispatcherStatus: vi.fn(), createInstance: vi.fn() }));
vi.mock('../../api/client', () => ({ api }));

beforeEach(() => {
  setLanguage('en');
  api.dispatcherStatus.mockResolvedValue({ running: true });
  api.createInstance.mockResolvedValue({});
});
afterEach(() => { cleanup(); setLanguage('en'); vi.restoreAllMocks(); vi.clearAllMocks(); });

describe('interface language preference', () => {
  it('switches live without remounting or translating user input and API values', async () => {
    const user = userEvent.setup();
    render(<><LanguageSelect /><MarkdownContent content={"Settings\n\n```text\nMemory\n```"} /><InstanceGrid instances={[]} onRefresh={vi.fn()} onViewLogs={vi.fn()} /></>);
    await screen.findByRole('button', { name: 'Pause Dispatcher' });
    await user.type(screen.getByRole('textbox'), 'Settings');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Interface language' }), 'zh-CN');
    expect(screen.getByRole('button', { name: '暂停调度' })).toBeInTheDocument();
    expect(screen.getByRole('textbox')).toHaveValue('Settings');
    expect(screen.getByText('Settings', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('Memory', { selector: 'code' })).toBeInTheDocument();
    expect(localStorage.getItem('ccm-language')).toBe('zh-CN');
    expect(document.documentElement.lang).toBe('zh-CN');
    expect(api.dispatcherStatus).toHaveBeenCalledOnce();
    await user.click(screen.getByRole('button', { name: '添加' }));
    await waitFor(() => expect(api.createInstance).toHaveBeenCalledWith({ name: 'Settings' }));
    await user.selectOptions(screen.getByRole('combobox'), 'en');
    expect(screen.getByRole('button', { name: 'Pause Dispatcher' })).toBeInTheDocument();
  });

  it('follows cross-tab changes and clears invalid stored preferences safely', () => {
    render(<LanguageSelect />);
    act(() => {
      localStorage.setItem('ccm-language', 'zh-CN');
      window.dispatchEvent(new StorageEvent('storage', { key: 'ccm-language' }));
    });
    expect(screen.getByRole('combobox')).toHaveValue('zh-CN');
    act(() => {
      localStorage.setItem('ccm-language', 'unsupported');
      window.dispatchEvent(new StorageEvent('storage', { key: 'ccm-language' }));
    });
    expect(getLanguage()).toBe('en');
    expect(screen.getByRole('combobox')).toHaveValue('en');
  });

  it('remains usable for the session when browser storage is blocked', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    render(<LanguageSelect />);
    await userEvent.selectOptions(screen.getByRole('combobox'), 'zh-CN');
    expect(screen.getByRole('combobox')).toHaveValue('zh-CN');
    expect(document.documentElement.lang).toBe('zh-CN');
  });
});
