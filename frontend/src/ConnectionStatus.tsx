import { useEffect, useState } from "react";

export function ConnectionStatus() {
  const [offline, setOffline] = useState(!navigator.onLine);
  useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  return offline ? (
    <p className="connection-status" role="status">
      You are offline. Displayed data may be out of date. Changes require a connection and are not queued.
    </p>
  ) : null;
}
