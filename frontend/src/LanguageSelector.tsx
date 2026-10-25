import { useSyncExternalStore } from "react";
import { getLanguage, setLanguage, subscribeLanguage, t } from "./i18n";
import type { Language } from "./i18n";

export function useLanguage() {
  return useSyncExternalStore(subscribeLanguage, getLanguage, getLanguage);
}

export function LanguageSelector() {
  const language = useLanguage();
  return (
    <label className="language-selector">
      <span>{t("Language")}</span>
      <select aria-label={t("Language")} value={language}
        onChange={(event) => setLanguage(event.target.value as Language)}>
        <option value="en" lang="en">English</option>
        <option value="zh-CN" lang="zh-CN">简体中文</option>
      </select>
    </label>
  );
}
