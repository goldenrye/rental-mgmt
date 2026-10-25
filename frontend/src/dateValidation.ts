/** Validate calendar dates without time-zone conversion or Date's rollover behavior. */
export function dateInputError(value: string, min = "0001-01-01", max = "9999-12-31"): string {
  if (!value) return "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "Enter a date in YYYY-MM-DD format.";
  const [year, month, day] = value.split("-").map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1]) {
    return "Enter a valid calendar date in YYYY-MM-DD format.";
  }
  if (value < min) return `Enter a date on or after ${min}.`;
  if (value > max) return `Enter a date on or before ${max}.`;
  return "";
}
