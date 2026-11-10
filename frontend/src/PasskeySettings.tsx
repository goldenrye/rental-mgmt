import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { PasskeyRecord } from "./api";
import { passkeyError, registerPasskey, supportsPasskeys } from "./passkeys";
import { t } from "./i18n";

function usePasskeyAvailability() {
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.all([api.passkeyStatus(), supportsPasskeys()]).then(([server, device]) => {
      if (active) setAvailable(server.available && device);
    }).catch(() => {});
    return () => { active = false; };
  }, []);
  return available;
}

export function PasskeyLogin({ loading, onLogin }: { loading: boolean; onLogin: () => void }) {
  const available = usePasskeyAvailability();
  return (
    <div className="form-section">
      <button type="button" className="secondary" disabled={loading || !available} onClick={onLogin}>
        {t("Sign in with a passkey")}
      </button>
      <p className="help-text">{t(available
        ? "Use Face ID, Touch ID, or your device screen lock. Register a passkey in your profile first."
        : "Passkey sign-in needs server setup and a supported browser or updated iPhone app.")}</p>
    </div>
  );
}

export function PasskeySettings({ loading, onBusyChange }: {
  loading: boolean; onBusyChange: (busy: boolean) => void;
}) {
  const available = usePasskeyAvailability();
  const [keys, setKeys] = useState<PasskeyRecord[]>([]);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const busy = useRef(false);

  useEffect(() => {
    let active = true;
    api.listPasskeys().then((items) => { if (active) setKeys(items); }).catch(() => {
      if (active) setError("Unable to load passkeys.");
    });
    return () => { active = false; };
  }, []);

  async function change(key?: PasskeyRecord) {
    if (busy.current || loading) return;
    setError("");
    setMessage("");
    if (!password) { setError("Enter your current password to manage passkeys."); return; }
    if (!key && !name.trim()) { setError("Enter a passkey name."); return; }
    if (key && !window.confirm(t("Remove this passkey? It will no longer sign in to this account."))) return;
    busy.current = true;
    onBusyChange(true);
    try {
      if (key) await api.removePasskey(key.id, password);
      else await registerPasskey(password, name.trim());
      setPassword("");
      if (!key) setName("");
      setKeys(await api.listPasskeys());
      setMessage(key ? "Passkey removed." : "Passkey registered. You can use it on the sign-in page.");
    } catch (err) {
      setError(passkeyError(err));
    } finally {
      busy.current = false;
      onBusyChange(false);
    }
  }

  return (
    <section className="form-section" aria-labelledby="passkeys-heading" aria-busy={loading}>
      <h3 id="passkeys-heading">{t("Passkeys")}</h3>
      <p className="muted">{t("Sign in with Face ID, Touch ID, or your device screen lock, even after signing out. Password login stays available. Password resets remove registered passkeys.")}</p>
      {keys.map((key) => (
        <div className="form-section" key={key.id}>
          <strong>{key.name}</strong>
          <button type="button" className="secondary" disabled={loading} onClick={() => void change(key)}
            aria-label={t("Remove passkey {{name}}", { name: key.name })}>{t("Remove Passkey")}</button>
        </div>
      ))}
      <label>{t("Passkey name")}
        <input value={name} maxLength={80} disabled={loading || !available}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }} />
      </label>
      <label>{t("Current password to manage passkeys")}
        <input type="password" autoComplete="current-password" maxLength={72} value={password}
          disabled={loading} onChange={(event) => setPassword(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }} />
      </label>
      <button type="button" className="secondary" disabled={loading || !available} onClick={() => void change()}>
        {t("Create a Passkey")}
      </button>
      {!available && <p className="help-text">{t("Passkey sign-in needs server setup and a supported browser or updated iPhone app.")}</p>}
      {error && <p className="alert error" role="alert">{t(error)}</p>}
      {message && <p className="alert success" role="status">{t(message)}</p>}
    </section>
  );
}
