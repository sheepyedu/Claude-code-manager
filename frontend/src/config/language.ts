import { useSyncExternalStore } from 'react';
import { zhCN } from './translations/zh-CN';

export type Language = 'en' | 'zh-CN';
const STORAGE_KEY = 'ccm-language';
const CHANGE_EVENT = 'ccm-language-change';
const normalize = (value: string | null): Language => value === 'zh-CN' ? 'zh-CN' : 'en';
function readSaved(): Language {
  try { return normalize(localStorage.getItem(STORAGE_KEY)); } catch { return 'en'; }
}
let current = readSaved();
if (typeof document !== 'undefined') document.documentElement.lang = current;

export function getLanguage(): Language { return current; }

export function setLanguage(language: Language) {
  current = normalize(language);
  try { localStorage.setItem(STORAGE_KEY, current); } catch { /* Session-only preference. */ }
  document.documentElement.lang = current;
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

function subscribe(listener: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY || event.key === null) {
      current = readSaved();
      document.documentElement.lang = current;
      listener();
    }
  };
  window.addEventListener(CHANGE_EVENT, listener);
  window.addEventListener('storage', onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    window.removeEventListener('storage', onStorage);
  };
}

export function useLanguage(): Language {
  return useSyncExternalStore(subscribe, getLanguage, () => 'en');
}

/** Translate authored UI strings only. Never pass chat bodies, code, or file content. */
export function t(source: string, values: unknown[] = []): string {
  const key = source.trim();
  const translated = current === 'zh-CN' ? zhCN[key] ?? key : key;
  const padded = (source.match(/^\s*/)?.[0] ?? '') + translated + (source.match(/\s*$/)?.[0] ?? '');
  return padded.replace(/\{(\d+)\}/g, (placeholder, index) => Number(index) < values.length ? String(values[Number(index)]) : placeholder);
}
