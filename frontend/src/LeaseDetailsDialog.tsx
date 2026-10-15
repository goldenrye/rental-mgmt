import { useEffect, useRef } from "react";
import type { Lease } from "./api";
import { DataTable } from "./PaginatedEntries";

type Props = {
  lease: Lease;
  propertyName: string;
  unitName: string;
  tenantNames: ReadonlyMap<number, string>;
  onClose: () => void;
};

export function LeaseDetailsDialog({ lease, propertyName, unitName, tenantNames, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
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
      className="card property-dialog lease-details-dialog"
      aria-labelledby="lease-details-title"
      onCancel={(event) => { event.preventDefault(); onClose(); }}
    >
      <div className="form">
        <div className="card-header">
          <h2 id="lease-details-title">Lease #{lease.id}</h2>
          <button type="button" className="secondary" autoFocus onClick={onClose}>Close</button>
        </div>
        <div className="detail-grid two-columns">
          <div><span>Property</span><strong>{propertyName}</strong></div>
          <div><span>Unit</span><strong>{unitName}</strong></div>
          <div><span>Start Date</span><strong>{lease.start_date}</strong></div>
          <div><span>End Date</span><strong>{lease.end_date}</strong></div>
          <div><span>Monthly Rent</span><strong>${lease.monthly_rent}</strong></div>
          <div><span>Tenants</span><strong>{lease.tenants.length}</strong></div>
        </div>
        <div>
          <h3>Notes</h3>
          <p className="lease-detail-notes">{lease.notes || "-"}</p>
        </div>
        <div>
          <h3>Tenants</h3>
          <DataTable
            label={`Tenants in lease ${lease.id}`}
            empty="No tenants in this lease."
            headers={["Tenant", "Monthly Rent Share", "Deposit"]}
            className="compact-table lease-tenant-table"
          >
            {lease.tenants.map((tenant) => (
              <tr key={tenant.tenant_id}>
                <td>{tenantNames.get(tenant.tenant_id) ?? `Tenant #${tenant.tenant_id}`}</td>
                <td className="numeric">${tenant.monthly_rent}</td>
                <td className="numeric">${tenant.deposit}</td>
              </tr>
            ))}
          </DataTable>
        </div>
      </div>
    </dialog>
  );
}
