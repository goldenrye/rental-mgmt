import { useState } from "react";
import { invitationCode } from "./runtimeConfig";
import { t } from "./i18n";

export function InvitationEntry({ onApply }: { onApply: (code: string) => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  return (
    <details className="invitation-entry">
      <summary>{t("Have an invitation?")}</summary>
      <form className="form" onSubmit={(event) => {
        event.preventDefault();
        try {
          onApply(invitationCode(value));
          setValue("");
          setError("");
        } catch {
          setError("Paste a valid invitation link or code from your administrator.");
        }
      }}>
        <label>{t("Invitation link or code")}
          <input required autoCapitalize="none" autoCorrect="off" spellCheck={false} value={value} onChange={(event) => setValue(event.target.value)} />
        </label>
        <p className="muted">{t("Use the same invitation your administrator shared for the website.")}</p>
        {error && <p role="alert" className="alert error">{t(error)}</p>}
        <button className="secondary">{t("Use Invitation")}</button>
      </form>
    </details>
  );
}
