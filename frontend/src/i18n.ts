import { createInstance } from "i18next";
import { translations } from "./translations.ts";

export type Language = "en" | "zh-CN";
export const languageStorageKey = "rental_mgmt_language";

export function detectLanguage(saved: string | null, languages: readonly string[]): Language {
  if (saved === "en" || saved === "zh-CN") return saved;
  for (const language of languages) {
    if (/^zh(?:-|$)/i.test(language)) return "zh-CN";
    if (/^en(?:-|$)/i.test(language)) return "en";
  }
  return "en";
}

function initialLanguage(): Language {
  let saved: string | null = null;
  try { saved = globalThis.localStorage?.getItem(languageStorageKey) ?? null; } catch { /* Storage may be blocked. */ }
  return detectLanguage(saved, typeof navigator === "undefined" ? [] : navigator.languages);
}

const i18n = createInstance();
void i18n.init({
  lng: initialLanguage(),
  fallbackLng: "en",
  supportedLngs: ["en", "zh-CN"],
  resources: { "zh-CN": { translation: translations } },
  keySeparator: false,
  nsSeparator: false,
  initAsync: false,
  interpolation: { escapeValue: false },
});

export function getLanguage(): Language {
  return i18n.language === "zh-CN" ? "zh-CN" : "en";
}

export function setLanguage(language: Language) {
  try { globalThis.localStorage?.setItem(languageStorageKey, language); } catch { /* Keep the in-memory choice. */ }
  void i18n.changeLanguage(language);
}

export function subscribeLanguage(listener: () => void) {
  i18n.on("languageChanged", listener);
  return () => { i18n.off("languageChanged", listener); };
}

/** Only interface strings belong here. Never pass user-entered content to t(). */
export function t(key: string, values?: Record<string, string | number>): string {
  return i18n.t(key, { ...values, defaultValue: key });
}

function updateDocument() {
  if (typeof document !== "undefined") {
    document.documentElement.lang = getLanguage();
    document.title = t("Rental Management");
  }
}
subscribeLanguage(updateDocument);
updateDocument();
