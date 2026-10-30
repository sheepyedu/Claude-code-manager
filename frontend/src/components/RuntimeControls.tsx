import { t, useLanguage } from '../config/language';
import { Brain, Gauge } from './icons';
import type { CodexServiceTier } from '../api/client';

interface Props {
  compact?: boolean;
  provider: string;
  effort: string | null;
  serviceTier?: CodexServiceTier;
  effortOptions: string[];
  defaultEffort?: string;
  supportsFast: boolean;
  fastUnavailableReason?: string;
  disabled?: boolean;
  effortLabel?: string;
  speedLabel?: string;
  onEffortChange: (effort: string) => void;
  onSpeedChange: (tier: CodexServiceTier) => void;
}

/** Only authored labels are translated; native effort/tier values stay intact. */
export function RuntimeControls({ compact = false, provider, effort, serviceTier = 'default', effortOptions,
  defaultEffort = 'medium', supportsFast, fastUnavailableReason, disabled, effortLabel = 'Reasoning effort',
  speedLabel, onEffortChange, onSpeedChange }: Props) {
  useLanguage();
  const options = [...new Set([...(effort ? [effort] : []), ...effortOptions])];
  const speedDisabled = disabled || provider !== 'codex';
  const effortDisabled = disabled || !options.length;
  const speedHint = `${t('Speed')}: ${t(provider === 'codex' && serviceTier === 'priority' ? 'Fast' : 'Standard')} · ${t(provider !== 'codex' ? 'This provider uses Standard speed' : 'Fast uses more quota')}`;
  const effortHint = `${t('Reasoning effort')}: ${effort ? t(effort) : `${t(defaultEffort)} ${t('(default)')}`}`;
  const iconClass = 'relative inline-flex shrink-0 items-center justify-center rounded-lg p-2 transition-colors focus-within:ring-2 focus-within:ring-blue-400';
  const selectClass = compact
    ? 'absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed'
    : 'rounded border border-gray-600 bg-gray-700 px-2 py-1 text-foreground disabled:opacity-50';
  return <div className={compact ? 'inline-flex shrink-0 items-center gap-1' : 'flex flex-wrap items-center gap-x-3 gap-y-2 text-xs'} data-runtime-controls data-runtime-compact={compact || undefined}>
    <label className={compact ? `${iconClass} ${speedDisabled ? 'text-gray-500 opacity-40' : serviceTier === 'priority' ? 'text-amber-400 hover:text-amber-300' : 'text-gray-500 hover:text-gray-300'}` : 'inline-flex items-center gap-1.5 text-gray-400'}>
      {compact ? <Gauge size={18} aria-hidden="true" /> : <span>{t('Speed')}</span>}
      <select aria-label={t(speedLabel || (provider === 'codex' ? 'Codex speed' : 'Claude speed'))}
        value={provider === 'codex' ? serviceTier : 'default'}
        disabled={speedDisabled}
        onChange={(event) => onSpeedChange(event.target.value as CodexServiceTier)}
        title={speedHint}
        className={selectClass}>
        <option value="default">{t('Standard')}</option>
        {provider === 'codex' && <option value="priority" disabled={!supportsFast}>{t('Fast')}</option>}
      </select>
    </label>
    <label className={compact ? `${iconClass} ${effortDisabled ? 'text-gray-500 opacity-40' : effort && effort !== defaultEffort ? 'text-indigo-400 hover:text-indigo-300' : 'text-gray-500 hover:text-gray-300'}` : 'inline-flex items-center gap-1.5 text-gray-400'}>
      {compact ? <Brain size={18} aria-hidden="true" /> : <span>{t('Reasoning effort')}</span>}
      <select aria-label={t(effortLabel)} value={effort || ''} disabled={effortDisabled} title={effortHint}
        onChange={(event) => onEffortChange(event.target.value)}
        className={selectClass}>
        <option value="">{t(defaultEffort)} {t('(default)')}</option>
        {options.filter((value) => value !== defaultEffort || effort === value).map((value) =>
          <option key={value} value={value}>{t(value)}</option>)}
      </select>
    </label>
    {!compact && provider === 'codex' && !supportsFast && fastUnavailableReason && <span className="text-[10px] text-gray-500">{fastUnavailableReason}</span>}
    {!compact && provider === 'codex' && serviceTier === 'priority' && <span className="text-[10px] text-amber-400">{t('Fast uses more quota')}</span>}
  </div>;
}
