import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { GoogleCalendarStatus } from "./api";
import { isNative, runtimeConfig } from "./platform";

export function GoogleCalendarSettings({ loading, onBusyChange }: {
  loading: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const [status, setStatus] = useState<GoogleCalendarStatus | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const busy = useRef(false);

  useEffect(() => {
    let active = true;
    api.googleCalendarStatus().then(
      (value) => { if (active) setStatus(value); },
      () => { if (active) setError("Unable to load Google Calendar settings. Try refreshing status."); },
    );
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
      setError(err instanceof Error ? err.message : "Unable to update Google Calendar settings.");
    } finally {
      busy.current = false;
      onBusyChange(false);
    }
  }

  function connect() {
    if (busy.current || loading || isNative) return;
    const popup = window.open("about:blank", "_blank");
    if (!popup) {
      setError("Allow popups for this site, then try connecting again.");
      return;
    }
    popup.opener = null;
    void run(async () => {
      try {
        const result = await api.connectGoogleCalendar();
        const url = new URL(result.authorization_url);
        if (url.origin !== "https://accounts.google.com") throw new Error("Invalid Google authorization URL.");
        popup.location.replace(url.href);
        setMessage("Finish connecting in the Google tab, then refresh status here. Connecting does not enable exports.");
      } catch (err) {
        popup.close();
        throw err;
      }
    });
  }

  function toggle(enabled: boolean) {
    if (enabled && !window.confirm(
      "Export future rent bills, including tenant names, property names, and rent amounts, to Google Calendar? "
      + "Existing bills and utility charges will not be exported.",
    )) return;
    void run(async () => {
      setStatus(await api.setGoogleCalendarEnabled(enabled));
      setMessage(enabled
        ? "Automatic exports are on for newly generated rent bills."
        : "Automatic exports are off. Pending exports were cancelled; existing calendar events were kept.");
    });
  }

  return (
    <section className="form-section" aria-labelledby="google-calendar-heading" aria-busy={loading}>
      <h3 id="google-calendar-heading">Google Calendar: {status ? (status.enabled ? "On" : "Off") : "Loading..."}</h3>
      <p className="muted">Optional and off by default. Export new rent bills as all-day due-date events in a dedicated Rental Due Dates calendar. Tenant names, property names, and rent amounts are shared with Google only after you enable exports.</p>
      <p className="help-text">No historical backfill or utility charges. Later payments, edits, and deletions are not synchronized. Turning off cancels pending exports and keeps existing Google events.</p>
      {status && !status.available && <p className="muted">Google Calendar is not configured on this server. Ask your administrator to complete Google OAuth setup.</p>}
      {status?.connected && <p>Connected to {status.calendar_name}. Pending exports: {status.pending_exports}.</p>}
      {status?.last_synced_at && <p className="help-text">Last export: {new Date(status.last_synced_at).toLocaleString()}</p>}
      {status?.last_error && <p className="alert error" role="alert">{status.last_error}</p>}
      <label className="inline-checkbox">
        <input type="checkbox" checked={status?.enabled ?? false}
          disabled={loading || !status || (!status.enabled && (!status.available || !status.connected))}
          onChange={(event) => toggle(event.target.checked)} />
        Automatically sync new rental bills to Google Calendar
      </label>
      {isNative && <p className="help-text">Connect Google from the <a href={runtimeConfig.publicAppURL} target="_blank" rel="noopener noreferrer">rental website</a> in your browser, then return here to refresh status and enable exports.</p>}
      <div className="actions">
        <button type="button" className="secondary" onClick={connect}
          disabled={loading || !status?.available || status.enabled || isNative}>
          {status?.connected ? "Reconnect Google Calendar" : "Connect Google Calendar"}
        </button>
        <button type="button" className="secondary" disabled={loading} onClick={() => void run(async () => {
          setStatus(await api.googleCalendarStatus());
        })}>Refresh Calendar Status</button>
        {status?.connected && <button type="button" className="danger" disabled={loading} onClick={() => {
          if (!window.confirm("Disconnect Google Calendar and stop exports? Existing calendar events will be kept.")) return;
          void run(async () => {
            await api.disconnectGoogleCalendar();
            setStatus(await api.googleCalendarStatus());
            setMessage("Disconnected. Stored Google credentials were removed. Existing events were kept.");
          });
        }}>Disconnect Google Calendar</button>}
      </div>
      {error && <p className="alert error" role="alert">{error}</p>}
      {message && <p className="alert success" role="status">{message}</p>}
    </section>
  );
}
