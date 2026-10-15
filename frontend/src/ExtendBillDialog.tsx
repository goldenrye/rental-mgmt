import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
import type { PropertyBill, PropertyBillExtensionPreview } from "./api";
import { billRecurrenceLabels } from "./propertyBillFilters";

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
        <h2 id="extend-bill-title">Extend Repeated Bill</h2>
        <p><strong>{bill.title}</strong> | {billRecurrenceLabels[bill.recurrence]} | ${bill.amount_due} per new bill</p>
        <p className="muted">
          Choose a new end date, then preview the existing future bills in this series and all proposed new bills.
          Existing bills and payments will not change. Tenant charges are not generated.
        </p>
        <div className="two-columns">
          <label>
            Extend through
            <input
              autoFocus required type="date" value={through} disabled={loading || saving}
              onChange={(event) => { setThrough(event.target.value); setPreview(null); }}
            />
          </label>
          <label>
            Due day for new bills (optional)
            <input
              type="number" min="1" max="31" step="1" placeholder="Use series due day"
              value={dueDay} disabled={loading || saving}
              onChange={(event) => { setDueDay(event.target.value); setPreview(null); }}
            />
          </label>
        </div>
        {error && <p className="alert error" role="alert">{error}</p>}
        {loading && <p role="status">Loading bill preview...</p>}
        {preview && (
          <>
            <p>{preview.property_name} | Current series end: {preview.current_end} | New end: {preview.through}</p>
            <p className="muted">Due day: {preview.due_day}, adjusted to month end for shorter months.</p>
            {preview.warning && <p role="note" className="muted">{preview.warning}</p>}
            <h3>Existing Future Bills ({preview.existing_future_bills.length})</h3>
            {preview.existing_future_bills.length ? (
              <div className="table-wrap">
                <table className="compact-table">
                  <thead><tr><th>Bill</th><th>Due Date</th><th>Amount Due</th><th>Status</th></tr></thead>
                  <tbody>{preview.existing_future_bills.map((existing) => (
                    <tr key={existing.id}>
                      <td>#{existing.id} {existing.title}</td><td>{existing.due_date}</td>
                      <td>${existing.amount_due}</td><td>{existing.status}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : <p>No existing bills in this series are due today or later.</p>}
            <h3>New Bills to Generate ({preview.new_bills.length})</h3>
            {preview.new_bills.length ? (
              <div className="table-wrap">
                <table className="compact-table">
                  <thead><tr><th>Due Date</th><th>Amount Due</th><th>Status</th></tr></thead>
                  <tbody>{preview.new_bills.map((proposed) => (
                    <tr key={proposed.due_date}>
                      <td>{proposed.due_date}</td><td>${proposed.amount_due}</td><td>New / unpaid</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : <p role="status">No new bills are due by this date. Choose a later end date.</p>}
            <p><strong>Total for new bills: ${preview.total_new_amount}</strong></p>
          </>
        )}
        <div className="modal-actions">
          <button type="button" className="secondary" disabled={loading || saving} onClick={onClose}>Cancel</button>
          <button type="submit" className="secondary" disabled={loading || saving || !through}>Preview Bills</button>
          <button type="button" className="primary" disabled={loading || saving || !preview?.new_bills.length} onClick={confirm}>
            {saving ? "Generating..." : "Confirm & Generate"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
