import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import { initializePlatform, isNative } from "./platform";
import { ConnectionStatus } from "./ConnectionStatus";

// The service worker lets the installed web app open offline; the iOS app bundles its own assets.
if (!isNative && import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

const root = ReactDOM.createRoot(document.getElementById("root")!);
root.render(<main className="auth-shell"><p role="status">Opening Rental Management...</p></main>);

initializePlatform().then(() => {
  root.render(<React.StrictMode><ConnectionStatus /><App /></React.StrictMode>);
}).catch(() => {
  root.render(
    <main className="auth-shell">
      <section className="auth-card">
        <h1>Unable to open secure storage</h1>
        <p role="alert">Unlock your device and try again. Your session has not been loaded.</p>
        <button className="primary" onClick={() => window.location.reload()}>Try Again</button>
      </section>
    </main>,
  );
});
