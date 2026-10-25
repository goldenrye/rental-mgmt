import { test } from "node:test";
import assert from "node:assert/strict";
import { dateInputError } from "./src/dateValidation.ts";

test("valid ISO dates and blank optional dates are accepted", () => {
  for (const value of ["", "0001-01-01", "2026-10-31", "2028-02-29", "2000-02-29", "9999-12-31"]) {
    assert.equal(dateInputError(value), "", value);
  }
});

test("impossible calendar dates and year zero are rejected", () => {
  for (const value of ["0000-01-01", "1900-02-29", "2100-02-29", "2026-02-29", "2026-04-31",
    "2026-00-01", "2026-13-01", "2026-01-00", "2026-01-32"]) {
    assert.notEqual(dateInputError(value), "", value);
  }
});

test("partial, ambiguous and non-ISO text cannot be submitted", () => {
  for (const value of ["2026-", "2026-1-1", "01/02/2026", "2026-10-01T00:00:00Z",
    " 2026-10-01", "2026-10-01 ", "10000-01-01", "tomorrow"]) {
    assert.match(dateInputError(value), /YYYY-MM-DD/);
  }
});

test("range limits are inclusive and enforced", () => {
  const min = "2026-10-01", max = "2026-10-31";
  assert.equal(dateInputError(min, min, max), "");
  assert.equal(dateInputError(max, min, max), "");
  assert.match(dateInputError("2026-09-30", min, max), /on or after/);
  assert.match(dateInputError("2026-11-01", min, max), /on or before/);
});
