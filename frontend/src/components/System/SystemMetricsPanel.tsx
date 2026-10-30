import { t as translateUI, useLanguage } from "../../config/language";
import { useCallback, useEffect, useState } from 'react';
import { Activity, HardDrive, Loader2, RefreshCw } from '../icons';
import { api } from '../../api/client';
import type { SystemMetricDisk, SystemMetrics } from '../../api/client';

function formatBytes(bytes: number): string {
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

function formatCollectedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function ProgressBar({ value, tone = 'indigo' }: { value: number; tone?: 'indigo' | 'green' | 'amber' }) {
  useLanguage();
  const colors = { indigo: 'bg-indigo-500', green: 'bg-green-500', amber: 'bg-amber-500' };
  return (
    <div className="h-2 overflow-hidden rounded-full bg-gray-700" aria-hidden="true">
      <div className={`h-full rounded-full ${colors[tone]} transition-[width]`} style={{ width: `${Math.max(0, Math.min(value, 100))}%` }} />
    </div>
  );
}

function DiskRow({ disk }: { disk: SystemMetricDisk }) {
  useLanguage();
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-400">{disk.path}</span>
        <span className="tabular-nums text-gray-300">{formatBytes(disk.used_bytes)} / {formatBytes(disk.total_bytes)} ({disk.used_percent.toFixed(1)}%)</span>
      </div>
      <ProgressBar value={disk.used_percent} tone={disk.used_percent >= 85 ? 'amber' : 'green'} />
    </div>
  );
}

export function SystemMetricsPanel() {
  useLanguage();
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async (force = false) => {
    if (force) setRefreshing(true);
    try {
      setMetrics(await api.systemMetrics(force));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <section className="space-y-3" aria-labelledby="system-metrics-heading">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 id="system-metrics-heading" className="text-foreground font-semibold">{translateUI("System health")}</h2>
          <p className="text-xs text-gray-500">{translateUI("CPU, memory and storage snapshot; updates hourly.")}</p>
        </div>
        <button
          type="button"
          onClick={() => void refresh(true)}
          disabled={loading || refreshing}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-700 bg-gray-800 px-2.5 py-1.5 text-xs font-medium text-gray-300 hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
          title={translateUI("Refresh system metrics now")}
        >
          {refreshing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          {translateUI("Refresh ")}</button>
      </div>

      {error && <div role="alert" className="rounded-md bg-red-500/15 px-3 py-2 text-sm text-red-400">{translateUI("Unable to load system metrics: ")}{error}</div>}
      {loading && !metrics && <div className="rounded-lg border border-gray-800 bg-gray-800/40 px-4 py-8 text-center text-sm text-gray-500">{translateUI("Loading system metrics…")}</div>}

      {metrics && (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
          <div className="rounded-lg border border-gray-700/60 bg-gray-800 p-4 space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-gray-200"><Activity size={16} className="text-indigo-400" /> {translateUI("CPU")}</div>
            <div className="flex items-baseline justify-between"><span className="text-2xl font-semibold tabular-nums text-foreground">{metrics.cpu.utilization_percent.toFixed(1)}%</span><span className="text-xs text-gray-500">{metrics.cpu.cores} {translateUI("cores · load ")}{metrics.cpu.load_1m.toFixed(2)}</span></div>
            <ProgressBar value={metrics.cpu.utilization_percent} />
          </div>
          <div className="rounded-lg border border-gray-700/60 bg-gray-800 p-4 space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-gray-200"><Activity size={16} className="text-amber-400" /> {translateUI("Memory")}</div>
            <div className="flex items-baseline justify-between"><span className="text-2xl font-semibold tabular-nums text-foreground">{metrics.memory.used_percent.toFixed(1)}%</span><span className="text-xs text-gray-500">{formatBytes(metrics.memory.used_bytes)} / {formatBytes(metrics.memory.total_bytes)}</span></div>
            <ProgressBar value={metrics.memory.used_percent} tone="amber" />
            <div className="flex justify-between text-xs text-gray-500"><span>{formatBytes(metrics.memory.available_bytes)} {translateUI("available")}</span><span>{translateUI("Swap ")}{metrics.memory.swap_used_percent.toFixed(1)}%</span></div>
          </div>
          <div className="rounded-lg border border-gray-700/60 bg-gray-800 p-4 space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-gray-200"><HardDrive size={16} className="text-green-400" /> {translateUI("Storage")}</div>
            <DiskRow disk={metrics.storage.root} />
            <DiskRow disk={metrics.storage.tmp} />
          </div>
        </div>
      )}
      {metrics && <p className="text-right text-[11px] text-gray-500">{translateUI("Collected ")}{formatCollectedAt(metrics.collected_at)}{metrics.cached ? translateUI(" · cached {0}m ago", [Math.floor(metrics.cache_age_seconds / 60)]) : translateUI(" · freshly sampled")}</p>}
    </section>
  );
}
