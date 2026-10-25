import { useEffect, useRef } from "react";
import type { InputHTMLAttributes } from "react";
import { dateInputError } from "./dateValidation";
import { t } from "./i18n";

function localizedDateError(value: string, min: string, max: string): string {
  const error = dateInputError(value, min, max);
  if (error === `Enter a date on or after ${min}.`) return t("Enter a date on or after {{date}}.", { date: min });
  if (error === `Enter a date on or before ${max}.`) return t("Enter a date on or before {{date}}.", { date: max });
  return t(error);
}

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "value" | "min" | "max"> & {
  value: string;
  min?: string;
  max?: string;
};

export function DateInput({
  value, onChange, min = "0001-01-01", max = "9999-12-31", disabled, readOnly, ...props
}: Props) {
  const text = useRef<HTMLInputElement>(null);
  const error = localizedDateError(value, min, max);
  useEffect(() => {
    text.current?.setCustomValidity(error);
  }, [error]);

  return (
    <span className="date-field">
      <input
        {...props}
        ref={text}
        type="text"
        placeholder="YYYY-MM-DD"
        title={t("Enter YYYY-MM-DD or use the calendar")}
        autoComplete="off"
        spellCheck={false}
        value={value}
        disabled={disabled}
        readOnly={readOnly}
        aria-invalid={error ? true : undefined}
        onChange={(event) => {
          event.currentTarget.setCustomValidity(localizedDateError(event.currentTarget.value, min, max));
          onChange?.(event);
        }}
      />
      <span className={`date-calendar${disabled || readOnly ? " date-calendar-disabled" : ""}`}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M7 3v4m10-4v4M3 11h18" />
        </svg>
        <input
          type="date"
          aria-label={t("Choose date with calendar")}
          title={t("Choose date with calendar")}
          min={min}
          max={max}
          value={error ? "" : value}
          disabled={disabled || readOnly}
          onClick={(event) => {
            // Keep the real native input clickable as a fallback on older iOS.
            try { event.currentTarget.showPicker?.(); } catch { /* Native interaction remains available. */ }
          }}
          onChange={(event) => {
            text.current?.setCustomValidity(localizedDateError(event.currentTarget.value, min, max));
            onChange?.(event);
          }}
        />
      </span>
    </span>
  );
}
