import { useRef, useState } from "react";
import { api } from "./api";
import type { User } from "./api";

export function EncryptionSettings({ user, loading, onBusyChange, onChanged }: {
  user: User;
  loading: boolean;
  onBusyChange: (busy: boolean) => void;
  onChanged: (user: User) => void;
}) {
  const [enabled, setEnabled] = useState(user.encryption_enabled);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const busy = useRef(false);
  const changed = enabled !== user.encryption_enabled;

  async function apply() {
    if (busy.current || loading || !changed) return;
    setError("");
    setMessage("");
    if (!password) {
      setError("Enter your current password to change this setting.");
      return;
    }
    const warning = enabled
      ? "Encrypt your existing and future sensitive rental data? This requires a server encryption key. Password resets still retain access. Old backups are not encrypted retroactively."
      : "Turn off encryption? Your existing and future rental data will be stored unencrypted and can be read directly from the database.";
    if (!window.confirm(warning)) return;
    busy.current = true;
    onBusyChange(true);
    try {
      const updated = await api.setEncryptionPreference(enabled, password);
      onChanged(updated);
      setEnabled(updated.encryption_enabled);
      setPassword("");
      setMessage(updated.encryption_enabled ? "Encryption is on. Existing records were encrypted." : "Encryption is off. Records are stored unencrypted.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to change encryption.");
    } finally {
      busy.current = false;
      onBusyChange(false);
    }
  }

  return (
    <section className="form-section" aria-labelledby="encryption-heading" aria-busy={loading}>
      <h3 id="encryption-heading">Data Encryption: {user.encryption_enabled ? "On" : "Off"}</h3>
      <p className="muted">Optional and off by default. Encrypt sensitive rental data before database storage. Account details and dates remain readable; admin password resets still retain access.</p>
      <label className="inline-checkbox">
        <input type="checkbox" checked={enabled} disabled={loading} onChange={(event) => {
          setEnabled(event.target.checked);
          setError("");
          setMessage("");
        }} />
        Encrypt my rental data
      </label>
      {changed && (
        <>
          <label>Current password to change encryption
            <input type="password" autoComplete="current-password" maxLength={72} value={password} disabled={loading}
              onChange={(event) => setPassword(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void apply();
                }
              }} />
          </label>
          <button type="button" className="secondary" disabled={loading} onClick={() => void apply()}>
            {busy.current ? "Converting records..." : "Apply Encryption Setting"}
          </button>
        </>
      )}
      {error && <p className="alert error" role="alert">{error}</p>}
      {message && <p className="alert success" role="status">{message}</p>}
    </section>
  );
}
