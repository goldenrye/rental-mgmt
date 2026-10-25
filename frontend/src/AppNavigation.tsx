import type { ReactNode } from "react";
import { t } from "./i18n";

export type Tab = "properties" | "tenants" | "leases" | "bills" | "bill";
const items: { tab: Tab; label: string; shortLabel: string; icon: ReactNode }[] = [
  { tab: "properties", label: "Properties & Units", shortLabel: "Properties", icon: <><path d="m3 10 9-7 9 7v11H3Z" /><path d="M9 21v-8h6v8" /></> },
  { tab: "tenants", label: "Tenants", shortLabel: "Tenants", icon: <><circle cx="9" cy="7" r="3" /><path d="M2 21v-3a7 7 0 0 1 14 0v3M17 4a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5v2" /></> },
  { tab: "leases", label: "Leases", shortLabel: "Leases", icon: <><path d="M14 3H5v18h14V8Z" /><path d="M14 3v5h5M8 12h8m-8 4h6" /></> },
  { tab: "bills", label: "Rental & Payments", shortLabel: "Rental", icon: <><rect x="2" y="5" width="20" height="15" rx="3" /><path d="M2 10h20m-7 5h4" /></> },
  { tab: "bill", label: "Property Bills", shortLabel: "Bills", icon: <><path d="M5 3h14v19l-3-2-4 2-4-2-3 2Z" /><path d="M8 8h8m-8 4h8m-8 4h4" /></> },
];

export function AppNavigation({ activeTab, onChange }: { activeTab: Tab; onChange: (tab: Tab) => void }) {
  return (
    <nav className="tabs app-navigation" aria-label={t("Main navigation")}>
      {items.map(({ tab, label, shortLabel, icon }) => (
        <button
          key={tab}
          type="button"
          className={activeTab === tab ? "active" : ""}
          aria-label={t(label)}
          aria-current={activeTab === tab ? "page" : undefined}
          onClick={() => {
            onChange(tab);
            if (window.matchMedia("(max-width: 700px), (hover: none) and (pointer: coarse) and (max-width: 1000px)").matches) {
              window.scrollTo({ top: 0 });
            }
          }}
        >
          <svg className="nav-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{icon}</svg>
          <span className="desktop-nav-label">{t(label)}</span>
          <span className="mobile-nav-label" aria-hidden="true">{t(shortLabel)}</span>
        </button>
      ))}
    </nav>
  );
}
