import { useEffect, useState } from 'react';
import { api, type SystemConfig, type Task } from '../../api/client';
import { RuntimeControls } from '../RuntimeControls';
import { t, useLanguage } from '../../config/language';

interface Props {
  task: Task;
  onUpdated: (task?: Task) => void;
  disabled?: boolean;
}

/** Persist the next turn's routing, with the same backend guards as Task Config. */
export function TaskRuntimeControls({ task, onUpdated, disabled }: Props) {
  useLanguage();
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const value = await api.config(); if (active) setConfig(value); }
      catch { if (active) setError(t('Unable to load model options')); }
    };
    void load();
    return () => { active = false; };
  }, []);
  if (task.mode === 'plan') return null;
  const codex = task.provider === 'codex';
  const model = task.model && task.model !== 'default' ? task.model
    : codex ? config?.default_codex_model || '' : config?.default_model || '';
  const efforts = codex ? config?.codex_model_efforts?.[model] || config?.codex_effort_options || []
    : config?.claude_model_efforts?.[model] || config?.effort_options || [];
  const blocked = disabled || saving || !config || !!task.shared_from_id
    || ['in_progress', 'executing', 'waiting_capability', 'migrating'].includes(task.status)
    || task.background_active;
  const update = async (patch: Parameters<typeof api.updateTask>[1]) => {
    if (blocked) return;
    setSaving(true);
    setError('');
    try {
      const updated = await api.updateTask(task.id, patch);
      onUpdated(updated);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('Unable to save runtime options'));
      onUpdated();
    } finally { setSaving(false); }
  };
  return <div className="space-y-1" onClick={(event) => event.stopPropagation()}>
    <RuntimeControls provider={task.provider} effort={task.effort_level}
      serviceTier={task.codex_service_tier || 'default'} defaultEffort={config?.default_effort}
      effortOptions={efforts} supportsFast={(config?.codex_model_service_tiers?.[model] || []).includes('priority')}
      disabled={!!blocked} onEffortChange={(effort) => void update({ effort_level: effort })}
      onSpeedChange={(tier) => void update({ codex_service_tier: tier })} />
    {error && <p role="alert" className="text-xs text-red-400">{error}</p>}
  </div>;
}
