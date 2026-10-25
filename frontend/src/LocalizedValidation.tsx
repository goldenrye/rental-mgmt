import { useEffect } from "react";
import { useLanguage } from "./LanguageSelector";
import { t } from "./i18n";

/** Translate browser validation without changing HTML constraints or date validation. */
export function LocalizedValidation() {
  const language = useLanguage();
  useEffect(() => {
    const localized = new Set<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>();
    function reset(event: Event) {
      const input = event.target;
      if (input instanceof HTMLInputElement || input instanceof HTMLSelectElement || input instanceof HTMLTextAreaElement) {
        if (localized.delete(input)) input.setCustomValidity("");
      }
    }
    function invalid(event: Event) {
      const input = event.target;
      if (language !== "zh-CN" || !(input instanceof HTMLInputElement || input instanceof HTMLSelectElement || input instanceof HTMLTextAreaElement)) return;
      if (input.validity.customError && !localized.has(input)) return;
      input.setCustomValidity("");
      const validity = input.validity;
      const message = validity.valueMissing ? t("Please fill out this field.")
        : validity.typeMismatch ? t("Please enter a valid email address.")
        : t("Please check this value.");
      input.setCustomValidity(message);
      localized.add(input);
    }
    document.addEventListener("invalid", invalid, true);
    document.addEventListener("input", reset, true);
    document.addEventListener("change", reset, true);
    return () => {
      document.removeEventListener("invalid", invalid, true);
      document.removeEventListener("input", reset, true);
      document.removeEventListener("change", reset, true);
      localized.forEach((input) => input.setCustomValidity(""));
    };
  }, [language]);
  return null;
}
