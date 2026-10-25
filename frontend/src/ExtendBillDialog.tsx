import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
import type { PropertyBill, PropertyBillExtensionPreview } from "./api";
import { billRecurrenceLabels } from "./propertyBillFilters";
import { DataTable } from "./PaginatedEntries";
import { DateInput } from "./DateInput";
import { t } from "./i18n";

type Props = {
  bill: PropertyBill;
  onClose: () => void;
  onGenerated: (bills: PropertyBill[]) => void;
};

export function ExtendBillDialog({ bill, onClose, onGenerated }: Props) {
  const [through, setThrough] = useState("");
  const [dueDay, setDueDay] = useState("");
  const [preview, setPreview] = useState<PropertyBillExtensionPreview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const busy = useRef(false);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    const trigger = document.activeElement;
    element?.showModal();
    return () => {
      element?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);

  async function loadPreview(event: FormEvent) {
    event.preventDefault();
    if (busy.current) return;
    busy.current = true;
    setLoading(true);
    setPreview(null);
    setError("");
    try {
      setPreview(await api.previewBillExtension(bill.id, through, dueDay ? Number(dueDay) : undefined));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load preview.");
    } finally {
      busy.current = false;
      setLoading(false);
    }
  }

  async function confirm() {
    if (!preview?.new_bills.length || busy.current) return;
    busy.current = true;
    setSaving(true);
    setError("");
    try {
      onGenerated(await api.extendPropertyBill(bill.id, preview));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to extend bills.");
      setPreview(null);
    } finally {
      busy.current = false;
      setSaving(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      className="card property-dialog extension-dialog"
      aria-labelledby="extend-bill-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy.current) onClose();
      }}
    >
      <form className="form" onSubmit={loadPreview} aria-busy={loading || saving}>
        <h2 id="extend-bill-title">{t("Extend Repeated Bill")}</h2>
        <p><strong>{bill.title}</strong> | {t(billRecurrenceLabels[bill.recurrence])} | {t("${{amount}} per new bill", { amount: bill.amount_due })}</p>
        <p className="muted">
          {t("Choose a new end date, then preview the existing future bills in this series and all proposed new bills. Existing bills and payments will not change. Tenant charges are not generated.")}
        </p>
        <div className="two-columns">
          <label>
            {t("Extend through")}
            <DateInput
              autoFocus required value={through} disabled={loading || saving}
              onChange={(event) => { setThrough(event.target.value); setPreview(null); }}
            />
          </label>
          <label>
            {t("Due day for new bills (optional)")}
            <input
              type="number" min="1" max="31" step="1" placeholder={t("Use series due day")}
              value={dueDay} disabled={loading || saving}
              onChange={(event) => { setDueDay(event.target.value); setPreview(null); }}
            />
          </label>
        </div>
        {error && <p className="alert error" role="alert">{t(error)}</p>}
        {loading && <p role="status">{t("Loading bill preview...")}</p>}
        {preview && (
          <>
            <p>{preview.property_name} | {t("Current series end: {{current}} | New end: {{next}}", { current: preview.current_end, next: preview.through })}</p>
            <p className="muted">{t("Due day: {{day}}, adjusted to month end for shorter months.", { day: preview.due_day })}</p>
            {preview.warning && <p role="note" className="muted">{t(preview.warning)}</p>}
            <h3>{t("Existing Future Bills ({{count}})", { count: preview.existing_future_bills.length })}</h3>
            {preview.existing_future_bills.length ? (
              <DataTable label="Existing future bills" empty="No existing future bills." headers={["Bill", "Due Date", "Amount Due", "Status"]} className="compact-table">
                  {preview.existing_future_bills.map((existing) => (
                    <tr key={existing.id}>
                      <td>#{existing.id} {existing.title}</td><td>{existing.due_date}</td>
                      <td>${existing.amount_due}</td><td>{t(existing.status)}</td>
                    </tr>
                  ))}
              </DataTable>
            ) : <p>{t("No existing bills in this series are due today or later.")}</p>}
            <h3>{t("New Bills to Generate ({{count}})", { count: preview.new_bills.length })}</h3>
            {preview.new_bills.length ? (
              <DataTable label="New bills to generate" empty="No new bills." headers={["Due Date", "Amount Due", "Status"]} className="compact-table">
                  {preview.new_bills.map((proposed) => (
                    <tr key={proposed.due_date}>
                      <td>{proposed.due_date}</td><td>${proposed.amount_due}</td><td>{t("New / unpaid")}</td>
                    </tr>
                  ))}
              </DataTable>
            ) : <p role="status">{t("No new bills are due by this date. Choose a later end date.")}</p>}
            <p><strong>{t("Total for new bills: ${{amount}}", { amount: preview.total_new_amount })}</strong></p>
            <p className="muted">{t("Confirmation generates all {{count}} new bills across all pages.", { count: preview.new_bills.length })}</p>
          </>
        )}
        <div className="modal-actions">
          <button type="button" className="secondary" disabled={loading || saving} onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="secondary" disabled={loading || saving || !through}>{t("Preview Bills")}</button>
          <button type="button" className="primary" disabled={loading || saving || !preview?.new_bills.length} onClick={confirm}>
            {t(saving ? "Generating..." : "Confirm & Generate")}
          </button>
        </div>
      </form>
    </dialog>
  );
}
