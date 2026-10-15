import assert from "node:assert/strict";
import test from "node:test";
import { billFilterError, billRecurrenceLabels, emptyPropertyBillFilters, filterPropertyBills } from "./src/propertyBillFilters.ts";

const bills = [
  { id: 1, property_id: 1, bill_type: "water", due_date: "2026-10-01", status: "unpaid", recurrence: "bi_monthly", generated_at: null },
  { id: 2, property_id: 2, bill_type: "electricity", due_date: "2026-10-31", status: "paid", recurrence: "monthly", generated_at: "2026-10-01T00:00:00Z" },
  { id: 3, property_id: 1, bill_type: "water", due_date: "2026-11-01", status: "paid", recurrence: "semi_annually" },
  { id: 4, property_id: 2, bill_type: "water", due_date: "2026-10-01", status: "unpaid", recurrence: "one_time", generated_at: null },
];
const filteredIds = (filters = {}) => filterPropertyBills(bills, { ...emptyPropertyBillFilters, ...filters }).map((bill) => bill.id);

test("quarterly bills have a label and can be filtered independently", () => {
  const quarterlyBill = { ...bills[0], id: 5, recurrence: "quarterly" };
  assert.equal(billRecurrenceLabels.quarterly, "Quarterly (every three months)");
  assert.deepEqual(
    filterPropertyBills([...bills, quarterlyBill], { ...emptyPropertyBillFilters, recurrence: "quarterly" }),
    [quarterlyBill],
  );
});

test("empty filters show all bills without changing source order", () => {
  assert.deepEqual(filteredIds(), [1, 2, 3, 4]);
  assert.deepEqual(bills.map((bill) => bill.id), [1, 2, 3, 4]);
});

test("date ranges include both boundaries and support open ends", () => {
  assert.deepEqual(filteredIds({ start: "2026-10-01", end: "2026-10-31" }), [1, 2, 4]);
  assert.deepEqual(filteredIds({ start: "2026-10-31" }), [2, 3]);
  assert.deepEqual(filteredIds({ end: "2026-10-01" }), [1, 4]);
  assert.deepEqual(filteredIds({ start: "2026-10-01", end: "2026-10-01" }), [1, 4]);
});

test("exact due date includes every bill on that date", () => {
  assert.deepEqual(filteredIds({ due_date: "2026-10-01" }), [1, 4]);
});

test("category and property filters work independently", () => {
  assert.deepEqual(filteredIds({ bill_type: "water" }), [1, 3, 4]);
  assert.deepEqual(filteredIds({ property_id: "1" }), [1, 3]);
  assert.deepEqual(filteredIds({ property_id: "999" }), []);
});

test("payment, recurrence and tenant bill filters work independently", () => {
  assert.deepEqual(filteredIds({ status: "paid" }), [2, 3]);
  assert.deepEqual(filteredIds({ status: "unpaid" }), [1, 4]);
  assert.deepEqual(filteredIds({ recurrence: "semi_annually" }), [3]);
  assert.deepEqual(filteredIds({ tenant_bills: "generated" }), [2]);
  assert.deepEqual(filteredIds({ tenant_bills: "not_generated" }), [1, 3, 4]);
});

test("all selected filters combine using AND", () => {
  assert.deepEqual(filteredIds({
    start: "2026-10-01", end: "2026-10-31", due_date: "2026-10-01",
    bill_type: "water", property_id: "1", status: "unpaid",
    recurrence: "bi_monthly", tenant_bills: "not_generated",
  }), [1]);
  assert.deepEqual(filteredIds({ bill_type: "water", property_id: "1", status: "unpaid", tenant_bills: "generated" }), []);
});

test("reset filters restore all results", () => {
  const filters = { ...emptyPropertyBillFilters, property_id: "1", status: "paid" };
  assert.equal(filterPropertyBills(bills, filters).length, 1);
  assert.equal(filterPropertyBills(bills, { ...emptyPropertyBillFilters }).length, 4);
  assert.equal(filters.property_id, "1");
});

test("reversed date range has a validation error", () => {
  assert.match(billFilterError({ ...emptyPropertyBillFilters, start: "2026-11-01", end: "2026-10-01" }), /End date/);
});

test("exact due dates outside the range have a validation error", () => {
  for (const due_date of ["2026-09-30", "2026-11-01"]) {
    assert.match(billFilterError({ ...emptyPropertyBillFilters, start: "2026-10-01", end: "2026-10-31", due_date }), /Exact due date/);
  }
});

test("blank and consistent date filters are valid", () => {
  assert.equal(billFilterError(emptyPropertyBillFilters), "");
  assert.equal(billFilterError({ ...emptyPropertyBillFilters, due_date: "2026-10-01" }), "");
  assert.equal(billFilterError({ ...emptyPropertyBillFilters, start: "2026-10-01", end: "2026-10-01", due_date: "2026-10-01" }), "");
});
