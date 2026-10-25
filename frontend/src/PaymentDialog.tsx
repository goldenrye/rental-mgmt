import { useEffect, useRef } from "react";
import type { Bill } from "./api";
import { t } from "./i18n";

type PaymentDraft = { amount: string; paid_at: string; note: string };

type Props = {
  bill: Bill;
  payment: PaymentDraft;
  saving: boolean;
  error: string;
  onChange: (payment: PaymentDraft) => void;
  onClose: () => void;
  onSubmit: () => Promise<void>;
};

export function PaymentDialog({ bill, payment, saving, error, onChange, onClose, onSubmit }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const submitting = useRef(false);

  useEffect(() => {
    const element = dialog.current!;
    const trigger = document.activeElement;
    element.showModal();
    return () => {
      element.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      className="card payment-dialog"
      aria-labelledby="payment-dialog-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!saving && !submitting.current) onClose();
      }}
    >
      <form
        className="form"
        aria-busy={saving}
        onSubmit={async (event) => {
          event.preventDefault();
          if (saving || submitting.current) return;
          submitting.current = true;
          try {
            await onSubmit();
          } finally {
            submitting.current = false;
          }
        }}
      >
        <h2 id="payment-dialog-title">{t("Record Payment")}</h2>
        <p className="muted">
          {t("Bill #{{id}}: {{title}} | {{tenant}}", { id: bill.id, title: bill.title, tenant: bill.tenant_name })}
          <br />
          {t("Outstanding balance: ${{balance}}", { balance: bill.balance })}
        </p>
        {error && <p className="alert error" role="alert">{t(error)}</p>}
        <label>
          {t("Payment amount ($)")}
          <input
            autoFocus
            required
            type="number"
            min="0.01"
            max={bill.balance}
            step="0.01"
            value={payment.amount}
            disabled={saving}
            onChange={(event) => onChange({ ...payment, amount: event.target.value })}
          />
        </label>
        <label>
          {t("Payment date and time (optional, defaults to now)")}
          <input
            type="datetime-local"
            value={payment.paid_at}
            disabled={saving}
            onChange={(event) => onChange({ ...payment, paid_at: event.target.value })}
          />
        </label>
        <label>
          {t("Note (optional)")}
          <input
            value={payment.note}
            disabled={saving}
            onChange={(event) => onChange({ ...payment, note: event.target.value })}
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="secondary" disabled={saving} onClick={onClose}>{t("Cancel")}</button>
          <button type="submit" className="primary" disabled={saving || !payment.amount}>
            {t(saving ? "Recording..." : "Record Payment")}
          </button>
        </div>
      </form>
    </dialog>
  );
}
