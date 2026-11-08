import { useEffect, useRef, useState } from "react";
import { biometricStatus, setBiometricEnabled } from "./platform";
import type { BiometricStatus } from "./platform";
import { t } from "./i18n";

export function BiometricSettings({ loading, onBusyChange }: {
  loading: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const [status, setStatus] = useState<BiometricStatus | null>(null);
  const [error, setError] = useState("");
  const busy = useRef(false);

  useEffect(() => {
    let active = true;
    biometricStatus().then((value) => {
      if (active) setStatus(value);
    }).catch(() => {
      if (active) setError("Unable to load biometric settings. Update the iPhone app and try again.");
    });
    return () => { active = false; };
  }, []);

  async function change(enabled: boolean) {
    if (busy.current || loading) return;
    busy.current = true;
    onBusyChange(true);
    setError("");
    try {
      setStatus(await setBiometricEnabled(enabled));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update biometric protection.");
    } finally {
      busy.current = false;
      onBusyChange(false);
    }
  }

  return (
    <section className="form-section" aria-labelledby="biometric-heading" aria-busy={loading || !status}>
      <h3 id="biometric-heading">{t("Biometric Unlock")}</h3>
      <p className="muted">{t("Optional and off by default. Use Face ID or Touch ID when opening the app and returning from the background. Your device passcode can be used as a fallback.")}</p>
      <label className="inline-checkbox">
        <input type="checkbox" checked={status?.enabled ?? false}
          disabled={loading || !status || (!status.available && !status.enabled)}
          onChange={(event) => void change(event.target.checked)} />
        {t("Require Face ID or Touch ID on this device")}
      </label>
      {status && !status.available && <p className="help-text">{t("Set up Face ID or Touch ID in iPhone Settings first.")}</p>}
      <p className="help-text">{t("Signing out turns this off. Expired sessions still require your account password. Your password and biometric data are never stored by this feature.")}</p>
      {error && <p className="alert error" role="alert">{t(error)}</p>}
    </section>
  );
}
