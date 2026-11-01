import { useEffect, useState } from "react";
import { useLanguage } from "./LanguageSelector";
import { t } from "./i18n";

export function TodayDate() {
  const language = useLanguage();
  const [today, setToday] = useState(() => new Date());

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    function refresh() {
      clearTimeout(timer);
      const now = new Date();
      setToday(now);
      // Calendar arithmetic respects local midnight, including daylight-saving changes.
      const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
      timer = setTimeout(refresh, midnight.getTime() - now.getTime() + 25);
    }
    refresh();
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  const date = new Intl.DateTimeFormat(language === "en" ? "en-US" : "zh-CN", {
    weekday: "short", year: "numeric", month: "short", day: "numeric",
  }).format(today);
  const localDate = [
    today.getFullYear(),
    String(today.getMonth() + 1).padStart(2, "0"),
    String(today.getDate()).padStart(2, "0"),
  ].join("-");

  return <p className="muted today-date"><time dateTime={localDate}>{t("Today: {{date}}", { date })}</time></p>;
}
