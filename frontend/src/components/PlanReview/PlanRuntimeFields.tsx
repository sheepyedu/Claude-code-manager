import { useEffect, useState } from 'react';
import { api, type PlanRuntimeOptions, type SystemConfig } from '../../api/client';
import { RuntimeControls } from '../RuntimeControls';
import { t, useLanguage } from '../../config/language';

export function PlanRuntimeFields({ value, onChange, disabled }: {
  value: PlanRuntimeOptions;
  onChange: (value: PlanRuntimeOptions) => void;
  disabled?: boolean;
}) {
  useLanguage();
  const [config, setConfig] = useState<SystemConfig | null>(null);
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const result = await api.config(); if (active) setConfig(result); }
      catch { /* Existing Managers without Plan capabilities keep their defaults. */ }
    };
    void load();
    return () => { active = false; };
  }, []);
  const pipeline = config?.plan_pipeline_defaults;
  return <div className="space-y-2 rounded-lg border border-gray-700 bg-gray-900/30 p-3">
    {(['planner', 'reviewer'] as const).map((stage) => {
      const route = pipeline?.[stage].primary;
      const provider = route?.provider || 'codex';
      const model = route?.model || '';
      const options = value[stage];
      const efforts = provider === 'codex' ? config?.codex_model_efforts?.[model] || config?.codex_effort_options || []
        : config?.claude_model_efforts?.[model] || config?.effort_options || [];
      const update = (patch: NonNullable<PlanRuntimeOptions['planner']>) => onChange({
        ...value, [stage]: { ...options, ...patch },
      });
      return <div key={stage} className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="w-16 text-xs font-medium text-gray-300">{t(stage === 'planner' ? 'Planner' : 'Reviewer')}</span>
        <span className="max-w-48 truncate text-[11px] text-gray-500" title={model}>{model}</span>
        <RuntimeControls provider={provider} effort={options?.effort || null}
          defaultEffort={route?.effort || config?.default_effort}
          serviceTier={options?.codex_service_tier ?? route?.codex_service_tier ?? 'default'}
          effortOptions={efforts} supportsFast={(config?.codex_model_service_tiers?.[model] || []).includes('priority')}
          disabled={disabled || !pipeline || (stage === 'reviewer' && !pipeline.reviewer.enabled)}
          speedLabel={stage === 'planner' ? 'Planner speed' : 'Reviewer speed'}
          effortLabel={stage === 'planner' ? 'Planner reasoning effort' : 'Reviewer reasoning effort'}
          onEffortChange={(effort) => update({ effort: effort || null })}
          onSpeedChange={(tier) => update({ codex_service_tier: tier })} />
      </div>;
    })}
    <p className="text-[10px] text-gray-500">{t('These choices tune the primary Planner and Reviewer; fallback routes keep global settings.')}</p>
  </div>;
}
