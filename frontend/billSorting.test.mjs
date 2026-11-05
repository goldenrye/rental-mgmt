import assert from "node:assert/strict";
import test from "node:test";
import { sortBillsByDueDate } from "./src/billSorting.ts";
import { paginateEntries } from "./src/pagination.ts";
import { emptyPropertyBillFilters, filterPropertyBills } from "./src/propertyBillFilters.ts";

test("bills sort earliest due date first without mutating the source", () => {
  const bills = Object.freeze([
    { id: 1, due_date: "2027-01-01" },
    { id: 3, due_date: "2026-10-31" },
    { id: 2, due_date: "2026-10-01" },
  ]);
  assert.deepEqual(sortBillsByDueDate(bills).map((bill) => bill.id), [2, 3, 1]);
  assert.deepEqual(bills.map((bill) => bill.id), [1, 3, 2]);
  assert.deepEqual(sortBillsByDueDate([]), []);
});

test("bills with the same due date sort by ID", () => {
  const bills = [12, 2, 7].map((id) => ({ id, due_date: "2026-10-31" }));
  assert.deepEqual(sortBillsByDueDate(bills).map((bill) => bill.id), [2, 7, 12]);
});

test("descending bills sort latest dates first, preserving ties and the source", () => {
  const bills = Object.freeze([
    { id: 4, due_date: "2025-12-31" },
    { id: 3, due_date: "2026-01-01" },
    { id: 2, due_date: "2026-01-01" },
    { id: 1, due_date: "2026-11-05" },
  ]);
  assert.deepEqual(sortBillsByDueDate(bills, "desc").map((bill) => bill.id), [1, 2, 3, 4]);
  assert.deepEqual(bills.map((bill) => bill.id), [4, 3, 2, 1]);
  assert.deepEqual(sortBillsByDueDate([], "desc"), []);
});

test("descending bills are sorted across the full list before pagination", () => {
  const bills = Array.from({ length: 15 }, (_, index) => ({
    id: index + 1, due_date: `2026-10-${String(index + 1).padStart(2, "0")}`,
  }));
  const sorted = sortBillsByDueDate(bills, "desc");
  assert.deepEqual(paginateEntries(sorted).entries.map((bill) => bill.id),
    [15, 14, 13, 12, 11, 10, 9, 8, 7, 6]);
  assert.deepEqual(paginateEntries(sorted, 2).entries.map((bill) => bill.id), [5, 4, 3, 2, 1]);
});

test("filtered bills are sorted before splitting into pages", () => {
  const bills = Array.from({ length: 15 }, (_, index) => ({
    id: index + 1, due_date: `2026-10-${String(15 - index).padStart(2, "0")}`,
  }));
  const sorted = sortBillsByDueDate(filterPropertyBills(bills, {
    ...emptyPropertyBillFilters, start: "2026-10-03",
  }));
  assert.equal(paginateEntries(sorted).entries[0].due_date, "2026-10-03");
  assert.equal(paginateEntries(sorted).entries.at(-1).due_date, "2026-10-12");
  assert.deepEqual(paginateEntries(sorted, 2).entries.map((bill) => bill.due_date),
    ["2026-10-13", "2026-10-14", "2026-10-15"]);
});

test("new and edited bills take their chronological positions", () => {
  const bills = [{ id: 1, due_date: "2026-11-01" }, { id: 2, due_date: "2026-12-01" }];
  assert.deepEqual(sortBillsByDueDate([...bills, { id: 3, due_date: "2026-10-01" }])
    .map((bill) => bill.id), [3, 1, 2]);
  assert.deepEqual(sortBillsByDueDate(bills.map((bill) =>
    bill.id === 2 ? { ...bill, due_date: "2026-09-01" } : bill,
  )).map((bill) => bill.id), [2, 1]);
});
