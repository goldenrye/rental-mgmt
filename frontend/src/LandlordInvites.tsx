import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
import type { LandlordInvite } from "./api";
import { DataTable } from "./PaginatedEntries";
import { copyText, runtimeConfig } from "./platform";
import { t } from "./i18n";

const dateTime = (value: string | null) => value ? new Date(value).toLocaleString() : "-";

const STATUS_LABELS: Record<LandlordInvite["status"], string> = {
  active: "Unused", used: "Used", expired: "Expired", revoked: "Revoked",
};

// The code travels in the URL fragment, which browsers never send to the server, so it stays out of access logs.
export const inviteLink = (code: string) => `${runtimeConfig.publicAppURL}/#invite=${encodeURIComponent(code)}`;

export function LandlordInvites() {
  const [invites, setInvites] = useState<LandlordInvite[]>([]);
  const [email, setEmail] = useState("");
  const [days, setDays] = useState(7);
  const [created, setCreated] = useState<{ link: string; email: string | null } | null>(null);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function run(action: () => Promise<void>) {
    setLoading(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update invitations.");
    } finally {
      setLoading(false);
    }
  }

  const refresh = () => run(async () => setInvites(await api.listInvites()));

  useEffect(() => {
    void refresh();
  }, []);

  function create(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      const invite = await api.createInvite({ email: email.trim() || undefined, expires_in_days: days });
      setCreated({ link: inviteLink(invite.code), email: invite.email });
      setCopied(false);
      setEmail("");
      setInvites(await api.listInvites());
    });
  }

  async function copy() {
    if (!created) return;
    try {
      await copyText(created.link);
      setCopied(true);
    } catch {
      setError("Copy failed. Select the link and copy it manually.");
    }
  }

  function revoke(invite: LandlordInvite) {
    if (!window.confirm(invite.email
      ? t("Revoke the invitation for {{email}}? The link will stop working.", { email: invite.email })
      : t("Revoke the invitation? The link will stop working."))) return;
    void run(async () => {
      await api.revokeInvite(invite.id);
      setInvites(await api.listInvites());
    });
  }

  return (
    <section className="card invite-card">
      <div className="card-header">
        <h2>{t("Landlord Invitations")}</h2>
        <button className="secondary" disabled={loading} onClick={() => void refresh()}>{t(loading ? "Loading..." : "Refresh")}</button>
      </div>
      <p className="muted admin-session-note">
        {t("Send a landlord an invitation link. Landlords can sign up only with an invitation link, and the account is active right away. Each link works once.")}
      </p>
      <form className="admin-filters" onSubmit={create}>
        <label>{t("Landlord email (optional)")}<input type="email" placeholder={t("Only this email can use the link")} value={email} disabled={loading} onChange={(event) => setEmail(event.target.value)} /></label>
        <label>{t("Expires after")}<select value={days} disabled={loading} onChange={(event) => setDays(Number(event.target.value))}>
          {[1, 3, 7, 14, 30].map((value) => <option key={value} value={value}>{t(value === 1 ? "{{count}} day" : "{{count}} days", { count: value })}</option>)}
        </select></label>
        <button className="primary invite-create" disabled={loading}>{t("Create Invite Link")}</button>
      </form>
      {error && <p role="alert" className="alert error">{t(error)}</p>}
      {created && (
        <div className="invite-link" role="status">
          <p><strong>{created.email ? t("Invitation link created for {{email}}.", { email: created.email }) : t("Invitation link created.")}</strong> {t("Copy it now; it is not shown again.")}</p>
          <div className="invite-link-row">
            <input readOnly aria-label={t("Invitation link")} value={created.link} onFocus={(event) => event.target.select()} />
            <button type="button" className="primary" onClick={() => void copy()}>{t(copied ? "Copied" : "Copy Link")}</button>
          </div>
        </div>
      )}
      <DataTable label="Landlord invitations" empty={loading ? "Loading invitations..." : "No invitations yet."}
        headers={["ID", "Email", "Status", "Created", "Expires", "Used By", "Actions"]} className="compact-table">
        {invites.map((invite) => (
          <tr key={invite.id}>
            <td>#{invite.id}</td><td>{invite.email ?? t("Any email")}</td>
            <td><span className={`status ${invite.status === "active" ? "paid" : invite.status === "used" ? "partial" : "unpaid"}`}>{t(STATUS_LABELS[invite.status])}</span></td>
            <td>{dateTime(invite.created_at)}</td><td>{dateTime(invite.expires_at)}</td>
            <td>{invite.used_by_email ? `${invite.used_by_email} (${dateTime(invite.used_at)})` : "-"}</td>
            <td><div className="compact-row-actions">
              {invite.status === "active" && <button className="danger" disabled={loading} aria-label={t("Revoke invitation #{{id}}", { id: invite.id })} onClick={() => revoke(invite)}>{t("Revoke")}</button>}
            </div></td>
          </tr>
        ))}
      </DataTable>
    </section>
  );
}
