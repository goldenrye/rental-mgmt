import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, storeToken } from "./api";
import type { LandlordAccount, User } from "./api";
import { LandlordInvites } from "./LandlordInvites";
import { DataTable } from "./PaginatedEntries";

const dateTime = (value: string | null) => value ? new Date(value).toLocaleString() : "-";

type Action = { account: LandlordAccount; kind: "status" | "password" };

function AccountActionDialog({ action, onClose, onSaved }: {
  action: Action;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const busy = useRef(false);
  const [saving, setSaving] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const { account, kind } = action;
  const label = kind === "password" ? "Reset Password" : account.is_active ? "Deactivate Landlord" : "Activate Landlord";

  useEffect(() => {
    const element = dialog.current!;
    const trigger = document.activeElement;
    element.showModal();
    return () => {
      element.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy.current) return;
    setError("");
    if (kind === "password" && password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }
    busy.current = true;
    setSaving(true);
    try {
      if (kind === "password") {
        await api.resetLandlordPassword(account.user_id, password);
        onSaved(`Password reset for ${account.email}. Existing sessions and MCP API keys were revoked.`);
      } else {
        await api.setLandlordActive(account.user_id, !account.is_active);
        onSaved(`${account.email} ${account.is_active ? "deactivated" : "activated"}.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update landlord.");
    } finally {
      busy.current = false;
      setSaving(false);
    }
  }

  return (
    <dialog ref={dialog} className="card property-dialog" aria-labelledby="admin-action-title" onCancel={(event) => {
      event.preventDefault();
      if (!busy.current) onClose();
    }}>
      <form className="form" onSubmit={submit} aria-busy={saving}>
        <h2 id="admin-action-title">{label}</h2>
        <p>{account.full_name} ({account.email})</p>
        <p className="muted">
          {kind === "password"
            ? "This replaces the password and revokes all API and MCP sessions, including MCP API keys. Share the new password securely with the landlord."
            : account.is_active
              ? "This blocks login and revokes all API and MCP sessions, including MCP API keys. Properties, tenants, and bills are preserved."
              : "This restores login access. Previously revoked sessions and MCP API keys remain invalid."}
        </p>
        {kind === "password" && (
          <>
            <label>New password<input autoFocus required type="password" autoComplete="new-password" minLength={8} maxLength={72} value={password} disabled={saving} onChange={(event) => setPassword(event.target.value)} /></label>
            <label>Confirm new password<input required type="password" autoComplete="new-password" minLength={8} maxLength={72} value={confirmation} disabled={saving} onChange={(event) => setConfirmation(event.target.value)} /></label>
          </>
        )}
        {error && <p role="alert" className="alert error">{error}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" disabled={saving} onClick={onClose}>Cancel</button>
          <button className={kind === "password" || account.is_active ? "danger" : "primary"} disabled={saving}>{saving ? "Saving..." : label}</button>
        </div>
      </form>
    </dialog>
  );
}

export function AdminDashboard({ user, onLogout, onUserChange }: {
  user: User;
  onLogout: () => Promise<void>;
  onUserChange: (user: User) => void;
}) {
  const [accounts, setAccounts] = useState<LandlordAccount[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [action, setAction] = useState<Action | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [showPassword, setShowPassword] = useState(user.password_change_required);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const savingPassword = useRef(false);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      setAccounts(await api.listLandlordAccounts());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load landlords.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!user.password_change_required) void refresh();
  }, [user.password_change_required]);

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    if (savingPassword.current) return;
    setError("");
    if (newPassword !== confirmation) {
      setError("Passwords do not match.");
      return;
    }
    savingPassword.current = true;
    setLoading(true);
    try {
      await api.changePassword({ current_password: currentPassword, new_password: newPassword });
      const token = await api.login({ email: user.email, password: newPassword });
      await storeToken(token.access_token);
      onUserChange(await api.me());
      setShowPassword(false);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmation("");
      setMessage("Admin password changed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to change password. Sign in again if the password was already changed.");
    } finally {
      savingPassword.current = false;
      setLoading(false);
    }
  }

  const visible = accounts.filter((account) =>
    `${account.full_name} ${account.email}`.toLowerCase().includes(search.trim().toLowerCase())
    && (status === "all" || account.is_active === (status === "active")));

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="header-greeting">
          <p className="eyebrow">Rental Management / Super Admin</p>
          <h1>Landlord Management</h1>
        </div>
        <div className="header-actions">
          {!user.password_change_required && <button className="secondary" disabled={loading} onClick={() => setShowPassword((value) => !value)}>Change Admin Password</button>}
          <button className="secondary" disabled={loading} onClick={() => void onLogout()}>Logout</button>
        </div>
      </header>
      {error && <p role="alert" className="alert error">{error}</p>}
      {message && <p role="status" className="alert success">{message}</p>}
      {(showPassword || user.password_change_required) && (
        <section className="card admin-password-card">
          <form className="form" onSubmit={changePassword}>
            <h2>{user.password_change_required ? "Secure Your Admin Account" : "Change Admin Password"}</h2>
            <p className="muted">Choose a new password before managing accounts. Use at least 8 characters. Changing it revokes existing admin sessions.</p>
            <label>Current password<input required type="password" autoComplete="current-password" value={currentPassword} disabled={loading} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
            <label>New password<input required minLength={8} maxLength={72} type="password" autoComplete="new-password" value={newPassword} disabled={loading} onChange={(event) => setNewPassword(event.target.value)} /></label>
            <label>Confirm new password<input required minLength={8} maxLength={72} type="password" autoComplete="new-password" value={confirmation} disabled={loading} onChange={(event) => setConfirmation(event.target.value)} /></label>
            <button className="primary" disabled={loading}>{loading ? "Saving..." : "Save Admin Password"}</button>
          </form>
        </section>
      )}
      {!user.password_change_required && <LandlordInvites />}
      {!user.password_change_required && (
        <section className="card">
          <div className="card-header">
            <h2>Landlords ({visible.length} of {accounts.length})</h2>
            <button className="secondary" disabled={loading} onClick={() => void refresh()}>{loading ? "Loading..." : "Refresh"}</button>
          </div>
          <div className="admin-filters">
            <label>Search<input type="search" placeholder="Name or email" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
            <label>Account status<select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="all">All accounts</option><option value="active">Active</option><option value="inactive">Deactivated</option>
            </select></label>
          </div>
          <p className="muted admin-session-note">Signed in means an unexpired session exists, not live browser presence. Refresh for the latest status. Login history starts when this feature is enabled.</p>
          <DataTable key={`${search}|${status}`} label="Landlord accounts" empty={loading ? "Loading landlords..." : "No matching landlords."}
            headers={["ID", "Name", "Email", "Account Status", "Login Status", "Sessions", "Last Login", "Last Activity", "Actions"]} className="compact-table">
            {visible.map((account) => (
              <tr key={account.user_id}>
                <td>#{account.user_id}</td><td>{account.full_name}</td><td>{account.email}</td>
                <td><span className={`status ${account.is_active ? "paid" : "unpaid"}`}>{account.is_active ? "Active" : account.login_status === "never_logged_in" ? "Pending approval" : "Deactivated"}</span></td>
                <td>{account.login_status === "signed_in" ? "Signed in" : account.login_status === "signed_out" ? "Signed out" : "No recorded login"}</td>
                <td>{account.active_sessions}</td><td>{dateTime(account.last_login_at)}</td><td>{dateTime(account.last_seen_at)}</td>
                <td><div className="compact-row-actions">
                  <button className={account.is_active ? "danger" : "secondary"} disabled={loading} aria-haspopup="dialog" aria-label={`${account.is_active ? "Deactivate" : "Activate"} ${account.email}`} onClick={() => setAction({ account, kind: "status" })}>{account.is_active ? "Deactivate" : "Activate"}</button>
                  <button disabled={loading} aria-haspopup="dialog" aria-label={`Reset password for ${account.email}`} onClick={() => setAction({ account, kind: "password" })}>Reset Password</button>
                </div></td>
              </tr>
            ))}
          </DataTable>
        </section>
      )}
      {action && <AccountActionDialog action={action} onClose={() => setAction(null)} onSaved={(notice) => {
        setAction(null);
        setMessage(notice);
        void refresh();
      }} />}
    </main>
  );
}
