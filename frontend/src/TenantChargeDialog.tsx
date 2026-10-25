import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { api } from "./api";
import type { Bill, TenantChargePreview } from "./api";
import { DataTable } from "./PaginatedEntries";
import { t } from "./i18n";

type Props = {
  billId: number;
  onClose: () => void;
  onGenerated: (bills: Bill[]) => void;
};

export function TenantChargeDialog({ billId, onClose, onGenerated }: Props) {
  const [preview, setPreview] = useState<TenantChargePreview | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [fetching, setFetching] = useState(true);
  const [saving, setSaving] = useState(false);
  const submitting = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    let cancelled = false;
    setFetching(true);
    setPreview(null);
    setError("");
    api.previewTenantCharges(billId)
      .then((result) => { if (!cancelled) setPreview(result); })
      .catch((err: Error) => { if (!cancelled) setError(err.message); })
      .finally(() => { if (!cancelled) setFetching(false); });
    return () => { cancelled = true; };
  }, [billId, refresh]);

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!preview?.tenants.length || submitting.current || fetching) return;
    submitting.current = true;
    setSaving(true);
    setError("");
    try {
      const bills = await api.generateTenantBillsFromPropertyBill(billId, preview.preview_token);
      onGenerated(bills);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to generate tenant bills.");
      setPreview(null);
    } finally {
      submitting.current = false;
      setSaving(false);
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !submitting.current) {
      event.stopPropagation();
      onClose();
    }
    if (event.key === "Tab") {
      const controls = event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === heading.current)) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="tenant-charge-title" onKeyDown={handleKeyDown}>
      <form className="card form modal-card" onSubmit={confirm} aria-busy={fetching || saving}>
        <h2 id="tenant-charge-title" ref={heading} tabIndex={-1}>{t("Confirm Tenant Bills")}</h2>
        {fetching && <p role="status">{t("Loading tenants and equal shares...")}</p>}
        {error && <p className="alert error" role="alert">{t(error)}</p>}
        {preview && (
          <>
            <p><strong>{preview.title}</strong> / {preview.property_name}</p>
            <p><strong>{t("Total: ${{amount}}", { amount: preview.amount_due })}</strong> | {t("Due date: {{date}}", { date: preview.due_date })}</p>
            <p className="muted">
              {t("Tenants with leases overlapping {{start}} through {{end}} are included once each. The total is split equally, with leftover cents distributed one cent at a time.", { start: preview.period_start, end: preview.period_end })}
            </p>
            {preview.tenants.length === 0 ? (
              <p role="status">{t("No eligible tenants found for this property bill period. Add or update the property's leases first.")}</p>
            ) : (
              <DataTable label="Tenant charge preview" empty="No eligible tenants." headers={["Tenant", "Amount to pay", "Due date"]}>
                    {preview.tenants.map((tenant) => (
                      <tr key={tenant.tenant_id}>
                        <td>{tenant.tenant_name}</td>
                        <td>${tenant.amount_due}</td>
                        <td>{preview.due_date}</td>
                      </tr>
                    ))}
              </DataTable>
            )}
            <p className="muted">
              {t("Confirm creates one bill per tenant, marked \"Tenant charge\", due on the last day of the original bill's due-date month. No bills are created until you confirm. Confirmation includes all {{count}} tenants across all pages.", { count: preview.tenants.length })}
            </p>
          </>
        )}
        <div className="modal-actions">
          <button type="button" className="secondary" disabled={fetching || saving} onClick={() => setRefresh((value) => value + 1)}>
            {t("Refresh Preview")}
          </button>
          <button type="button" className="secondary" disabled={saving} onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="primary" disabled={fetching || saving || !preview?.tenants.length}>
            {t(saving ? "Generating..." : "Confirm & Generate")}
          </button>
        </div>
      </form>
    </div>
  );
}
