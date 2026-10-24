import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { McpApiKey } from "./api";
import { copyText } from "./platform";
import { DataTable } from "./PaginatedEntries";

const dateTime = (value: string | null) => value ? new Date(value).toLocaleString() : "-";

export function McpApiKeysDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const busy = useRef(false);
  const [keys, setKeys] = useState<McpApiKey[]>([]);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [expiry, setExpiry] = useState("");
  const [secret, setSecret] = useState<{ id: string; value: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    await run(async () => setKeys(await api.listMcpApiKeys()));
  }

  async function run(action: () => Promise<void>) {
    if (busy.current) return;
    busy.current = true;
    setLoading(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to manage API keys.");
    } finally {
      busy.current = false;
      setLoading(false);
    }
  }

  useEffect(() => {
    const element = dialog.current!;
    const trigger = document.activeElement;
    element.showModal();
    void refresh();
    return () => {
      element.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);

  function close() {
    if (busy.current) return;
    if (secret && !window.confirm("Close this window? The full API key cannot be shown again. Make sure you saved it securely.")) return;
    onClose();
  }

  return (
    <dialog ref={dialog} className="card property-dialog extension-dialog" aria-labelledby="mcp-keys-title"
      onCancel={(event) => { event.preventDefault(); close(); }}>
      <div className="form" aria-busy={loading}>
        <div className="card-header">
          <h2 id="mcp-keys-title">MCP API Keys</h2>
          <button type="button" className="secondary" disabled={loading} onClick={close}>Close</button>
        </div>
        <p className="muted">Give each agent its own key. Keys can only read your rental data through MCP; they cannot sign in to the website or change records.</p>
        <p className="help-text">Keys survive normal login and logout. Password changes, administrator password resets, and account deactivation revoke all your keys.</p>
        {error && <p role="alert" className="alert error">{error}</p>}
        {secret && (
          <section className="form-section" aria-label="New API key">
            <strong>Save this key now. It is shown only once.</strong>
            <input aria-label="New MCP API key" readOnly value={secret.value} autoComplete="off" spellCheck={false}
              onFocus={(event) => event.target.select()} />
            <button type="button" className="primary" onClick={() => void run(async () => {
              await copyText(secret.value);
              setCopied(true);
            })} disabled={loading}>{copied ? "Copied" : "Copy API Key"}</button>
            <p className="help-text">Use Authorization: Bearer &lt;API_KEY&gt; or X-API-Key: &lt;API_KEY&gt; on your MCP connection. Do not put keys in URLs or share them in chat.</p>
            <button type="button" className="secondary" disabled={loading} onClick={() => {
              if (window.confirm("Have you saved the key? It cannot be retrieved again.")) setSecret(null);
            }}>I Saved the Key</button>
          </section>
        )}
        <form className="form form-section" onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            const created = await api.createMcpApiKey({
              name: name.trim(), current_password: password, expires_in_days: expiry ? Number(expiry) : null,
            });
            setSecret({ id: created.id, value: created.api_key });
            setCopied(false);
            setPassword("");
            setName("");
            const { api_key: _secret, ...metadata } = created;
            setKeys((current) => [metadata, ...current]);
          });
        }}>
          <label>Key name
            <input required maxLength={80} placeholder="For example, Claude Desktop" value={name}
              disabled={loading || Boolean(secret)} onChange={(event) => setName(event.target.value)} />
          </label>
          <label>Expiration
            <select value={expiry} disabled={loading || Boolean(secret)} onChange={(event) => setExpiry(event.target.value)}>
              <option value="">No expiration (until revoked)</option>
              {[30, 90, 365].map((days) => <option key={days} value={days}>{days} days</option>)}
            </select>
          </label>
          <label>Current password
            <input required type="password" maxLength={72} autoComplete="current-password" value={password}
              disabled={loading || Boolean(secret)} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button className="primary" disabled={loading || Boolean(secret) || !name.trim() || !password}>Create API Key</button>
        </form>
        <button type="button" className="secondary" disabled={loading} onClick={() => void refresh()}>Refresh Keys</button>
        <DataTable label="MCP API keys" empty={loading ? "Loading keys..." : "No API keys yet."}
          headers={["Name", "Prefix", "Status", "Created", "Expires", "Last Used", "Actions"]} className="compact-table">
          {keys.map((key) => <tr key={key.id}>
            <td>{key.name}</td><td>{key.prefix}...</td><td>{key.status}</td>
            <td>{dateTime(key.created_at)}</td><td>{key.expires_at ? dateTime(key.expires_at) : "Never"}</td>
            <td>{dateTime(key.last_used_at)}</td>
            <td>{key.status === "active" && <button type="button" className="secondary" disabled={loading}
              aria-label={`Revoke API key ${key.name}`} onClick={() => {
                if (!window.confirm(`Revoke "${key.name}"? Agents using this key will lose access.`)) return;
                void run(async () => {
                  await api.revokeMcpApiKey(key.id);
                  if (secret?.id === key.id) setSecret(null);
                  setKeys((current) => current.map((entry) => entry.id === key.id ? { ...entry, status: "revoked" } : entry));
                });
              }}>Revoke</button>}</td>
          </tr>)}
        </DataTable>
      </div>
    </dialog>
  );
}
