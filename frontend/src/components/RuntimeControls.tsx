import { t, useLanguage } from '../config/language';
import type { CodexServiceTier } from '../api/client';

interface Props {
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
export function RuntimeControls({ provider, effort, serviceTier = 'default', effortOptions,
  defaultEffort = 'medium', supportsFast, fastUnavailableReason, disabled, effortLabel = 'Reasoning effort',
  speedLabel, onEffortChange, onSpeedChange }: Props) {
  useLanguage();
  const options = [...new Set([...(effort ? [effort] : []), ...effortOptions])];
  return <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs" data-runtime-controls>
    <label className="inline-flex items-center gap-1.5 text-gray-400">
      <span>{t('Speed')}</span>
      <select aria-label={t(speedLabel || (provider === 'codex' ? 'Codex speed' : 'Claude speed'))}
        value={provider === 'codex' ? serviceTier : 'default'}
        disabled={disabled || provider !== 'codex'}
        onChange={(event) => onSpeedChange(event.target.value as CodexServiceTier)}
        title={provider !== 'codex' ? t('This provider uses Standard speed') : t('Fast uses more quota')}
        className="rounded border border-gray-600 bg-gray-700 px-2 py-1 text-foreground disabled:opacity-50">
        <option value="default">{t('Standard')}</option>
        {provider === 'codex' && <option value="priority" disabled={!supportsFast}>{t('Fast')}</option>}
      </select>
    </label>
    <label className="inline-flex items-center gap-1.5 text-gray-400">
      <span>{t('Reasoning effort')}</span>
      <select aria-label={t(effortLabel)} value={effort || ''} disabled={disabled || !options.length}
        onChange={(event) => onEffortChange(event.target.value)}
        className="rounded border border-gray-600 bg-gray-700 px-2 py-1 text-foreground disabled:opacity-50">
        <option value="">{t(defaultEffort)} {t('(default)')}</option>
        {options.filter((value) => value !== defaultEffort || effort === value).map((value) =>
          <option key={value} value={value}>{t(value)}</option>)}
      </select>
    </label>
    {provider === 'codex' && !supportsFast && fastUnavailableReason && <span className="text-[10px] text-gray-500">{fastUnavailableReason}</span>}
    {provider === 'codex' && serviceTier === 'priority' && <span className="text-[10px] text-amber-400">{t('Fast uses more quota')}</span>}
  </div>;
}
