import { getLanguage, setLanguage, t, useLanguage, type Language } from '../../config/language';

export function LanguageSelect() {
  useLanguage();
  return (
    <label className="flex items-center justify-between gap-3 text-xs text-gray-400">
      <span>{t('Language')}</span>
      <select
        aria-label={t('Interface language')}
        value={getLanguage()}
        onChange={(event) => setLanguage(event.target.value as Language)}
        className="bg-gray-700 text-gray-200 text-xs rounded-md px-2 py-1 border border-gray-600 focus:outline-none focus:ring-1 focus:ring-indigo-500"
      >
        <option value="en">English</option>
        <option value="zh-CN">简体中文</option>
      </select>
    </label>
  );
}
