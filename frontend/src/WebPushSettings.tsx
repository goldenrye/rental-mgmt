import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { WebPushStatus } from "./api";
import { isNative } from "./platform";
import { t } from "./i18n";

const supported = !isNative && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const iosBrowser = /iPhone|iPad|iPod/.test(navigator.userAgent) && !window.matchMedia("(display-mode: standalone)").matches;

function applicationServerKey(base64url: string) {
  const raw = atob(base64url.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(base64url.length / 4) * 4, "="));
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}

function sameKey(subscription: PushSubscription, key: Uint8Array) {
  const current = subscription.options.applicationServerKey;
  if (!current) return false;
  const bytes = new Uint8Array(current);
  return bytes.length === key.length && bytes.every((value, index) => value === key[index]);
}

async function currentSubscription() {
  const registration = supported ? await navigator.serviceWorker.getRegistration() : undefined;
  return { registration, subscription: registration ? await registration.pushManager.getSubscription() : null };
}

// Called on sign-out so a shared browser stops receiving this landlord's reminders.
export async function removeThisDeviceFromPush() {
  try {
    const { subscription } = await currentSubscription();
    if (!subscription) return;
    await api.removeWebPushSubscription(subscription.endpoint).catch(() => {});
    await subscription.unsubscribe();
  } catch {
    // Best effort: sign-out must not depend on the push service.
  }
}

export function WebPushSettings({ loading, onBusyChange }: {
  loading: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const [status, setStatus] = useState<WebPushStatus | null>(null);
  const [thisDevice, setThisDevice] = useState(false);
  const [workerReady, setWorkerReady] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const busy = useRef(false);

  useEffect(() => {
    let active = true;
    (async () => {
      const value = await api.webPushStatus();
      const { registration, subscription } = await currentSubscription();
      if (!active) return;
      setWorkerReady(Boolean(registration));
      setStatus(value);
      // Re-register an existing subscription: it may have rotated or belong to a previous sign-in.
      if (subscription && value.available && value.public_key && Notification.permission === "granted"
        && sameKey(subscription, applicationServerKey(value.public_key))) {
        const synced = await api.addWebPushSubscription(subscription.toJSON());
        if (active) {
          setStatus(synced);
          setThisDevice(true);
        }
      }
    })().catch(() => { if (active) setError("Unable to load notification settings."); });
    return () => { active = false; };
  }, []);

  async function run(action: () => Promise<void>) {
    if (busy.current || loading) return;
    busy.current = true;
    onBusyChange(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to update notification settings.");
    } finally {
      busy.current = false;
      onBusyChange(false);
    }
  }

  function enable() {
    void run(async () => {
      const { registration, subscription: existing } = await currentSubscription();
      if (!registration || !status?.public_key) throw new Error("Notifications are unavailable in this browser.");
      if (await Notification.requestPermission() !== "granted") {
        throw new Error("Notifications are blocked. Allow them for this site in your browser settings, then try again.");
      }
      const key = applicationServerKey(status.public_key);
      // A subscription made with an older server key cannot receive messages.
      if (existing && !sameKey(existing, key)) await existing.unsubscribe();
      const subscription = existing && sameKey(existing, key)
        ? existing
        : await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      setStatus(await api.addWebPushSubscription(subscription.toJSON()));
      setThisDevice(true);
      setMessage("Notifications are on for this device.");
    });
  }

  function disable() {
    void run(async () => {
      const { subscription } = await currentSubscription();
      if (subscription) {
        await api.removeWebPushSubscription(subscription.endpoint);
        await subscription.unsubscribe();
      }
      setStatus(await api.webPushStatus());
      setThisDevice(false);
      setMessage("Notifications are off for this device.");
    });
  }

  function setPreference(change: Partial<Pick<WebPushStatus, "lease_expiry" | "bill_due">>) {
    if (!status) return;
    void run(async () => {
      setStatus(await api.setWebPushPreferences({
        lease_expiry: change.lease_expiry ?? status.lease_expiry,
        bill_due: change.bill_due ?? status.bill_due,
      }));
    });
  }

  let unavailable = "";
  if (isNative) unavailable = "Notifications are not available in the iPhone app yet. Add the rental website to your Home Screen to receive them.";
  else if (!supported && iosBrowser) unavailable = "On iPhone and iPad, add this site to your Home Screen (Share > Add to Home Screen), then open it from there to turn on notifications.";
  else if (!supported) unavailable = "This browser does not support push notifications.";
  else if (status && !status.available) unavailable = "Notifications are not configured on this server. Ask your administrator to set a web push key.";
  else if (status && !workerReady) unavailable = "Notifications need the installed web app. Reload the page and try again.";

  return (
    <section className="form-section" aria-labelledby="web-push-heading" aria-busy={loading}>
      <h3 id="web-push-heading">{t("Notifications: {{status}}", { status: t(status ? (thisDevice ? "On for this device" : "Off for this device") : "Loading...") })}</h3>
      <p className="muted">
        {t("Reminders {{schedule}}. Each reminder is sent once. They show only counts and dates, never tenant names, properties, or amounts.", {
          schedule: status ? t("{{lease}} days before a lease ends and {{bill}} days before unpaid bills are due", { lease: status.lease_notice_days, bill: status.bill_notice_days }) : t("before leases end and bills are due"),
        })}
      </p>
      {unavailable && <p className="help-text">{t(unavailable)}</p>}
      {status && status.devices > 0 && <p className="help-text">{t("Devices receiving notifications: {{count}}.", { count: status.devices })}</p>}
      <label className="inline-checkbox">
        <input type="checkbox" checked={status?.lease_expiry ?? true} disabled={loading || !status}
          onChange={(event) => setPreference({ lease_expiry: event.target.checked })} />
        {t("Lease ending soon")}
      </label>
      <label className="inline-checkbox">
        <input type="checkbox" checked={status?.bill_due ?? true} disabled={loading || !status}
          onChange={(event) => setPreference({ bill_due: event.target.checked })} />
        {t("Bills due soon (property bills and unpaid tenant bills)")}
      </label>
      <div className="actions">
        {thisDevice ? (
          <>
            <button type="button" className="secondary" disabled={loading} onClick={() => void run(async () => {
              setStatus(await api.testWebPush());
              setMessage("Test notification sent.");
            })}>{t("Send Test Notification")}</button>
            <button type="button" className="danger" disabled={loading} onClick={disable}>{t("Turn Off on This Device")}</button>
          </>
        ) : (
          <button type="button" className="secondary" disabled={loading || !status || Boolean(unavailable)} onClick={enable}>
            {t("Turn On for This Device")}
          </button>
        )}
      </div>
      {error && <p className="alert error" role="alert">{t(error)}</p>}
      {message && <p className="alert success" role="status">{t(message)}</p>}
    </section>
  );
}
