import { useState } from "react";
import type { KeyboardEvent } from "react";
import type { RentalProperty } from "./api";
import {
  billCategoryLabels,
  billFilterError,
  billRecurrenceLabels,
  emptyPropertyBillFilters,
} from "./propertyBillFilters";
import type { PropertyBillFilters } from "./propertyBillFilters";
import { DateInput } from "./DateInput";
import { t } from "./i18n";

type Props = {
  filters: PropertyBillFilters;
  properties: RentalProperty[];
  onApply: (filters: PropertyBillFilters) => void;
  onClose: () => void;
};

export function PropertyBillFilterDialog({ filters, properties, onApply, onClose }: Props) {
  const [draft, setDraft] = useState(filters);
  const error = billFilterError(draft);

  function update<K extends keyof PropertyBillFilters>(field: K, value: PropertyBillFilters[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
    if (event.key === "Tab") {
      const controls = event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), input, select");
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
  }

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bill-filter-title"
      aria-describedby="bill-filter-help"
      onKeyDown={handleKeyDown}
    >
      <form
        className="card form modal-card"
        onSubmit={(event) => {
          event.preventDefault();
          if (!error) onApply(draft);
        }}
      >
        <h2 id="bill-filter-title">{t("Filter Property Bills")}</h2>
        <p className="muted" id="bill-filter-help">
          {t("All filters are combined. Start and end dates filter the due-date range, including both dates. Leave fields blank to include all values. Changes take effect after Apply Filters.")}
        </p>
        <div className="two-columns">
          <label>
            {t("Start date (due from)")}
            <DateInput autoFocus max="9999-12-31" value={draft.start} onChange={(event) => update("start", event.target.value)} />
          </label>
          <label>
            {t("End date (due through)")}
            <DateInput max="9999-12-31" value={draft.end} onChange={(event) => update("end", event.target.value)} />
          </label>
          <label>
            {t("Exact due date")}
            <DateInput max="9999-12-31" value={draft.due_date} onChange={(event) => update("due_date", event.target.value)} />
          </label>
          <label>
            {t("Bill category")}
            <select value={draft.bill_type} onChange={(event) => update("bill_type", event.target.value as PropertyBillFilters["bill_type"])}>
              <option value="">{t("All categories")}</option>
              {Object.entries(billCategoryLabels).map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}
            </select>
          </label>
          <label>
            {t("Property")}
            <select value={draft.property_id} onChange={(event) => update("property_id", event.target.value)}>
              <option value="">{t("All properties")}</option>
              {properties.map((property) => <option key={property.id} value={property.id}>{property.name}</option>)}
            </select>
          </label>
          <label>
            {t("Payment status")}
            <select value={draft.status} onChange={(event) => update("status", event.target.value as PropertyBillFilters["status"])}>
              <option value="">{t("All statuses")}</option>
              <option value="unpaid">{t("Unpaid")}</option>
              <option value="paid">{t("Paid")}</option>
            </select>
          </label>
          <label>
            {t("Repeat")}
            <select value={draft.recurrence} onChange={(event) => update("recurrence", event.target.value as PropertyBillFilters["recurrence"])}>
              <option value="">{t("All repeat options")}</option>
              {Object.entries(billRecurrenceLabels).map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}
            </select>
          </label>
          <label>
            {t("Tenant bills")}
            <select value={draft.tenant_bills} onChange={(event) => update("tenant_bills", event.target.value as PropertyBillFilters["tenant_bills"])}>
              <option value="">{t("All tenant bill states")}</option>
              <option value="generated">{t("Generated")}</option>
              <option value="not_generated">{t("Not generated")}</option>
            </select>
          </label>
        </div>
        {error && <p className="error" role="alert">{t(error)}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={() => setDraft({ ...emptyPropertyBillFilters })}>
            {t("Reset Filters")}
          </button>
          <button type="button" className="secondary" onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="primary" disabled={Boolean(error)}>{t("Apply Filters")}</button>
        </div>
      </form>
    </div>
  );
}
