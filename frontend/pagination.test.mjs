import assert from "node:assert/strict";
import test from "node:test";
import { Children, Fragment, createElement } from "react";
import { paginateEntries, PAGE_SIZE } from "./src/pagination.ts";

const entries = Array.from({ length: 25 }, (_, index) => ({ id: index + 1 }));

test("lists default to the first ten entries without changing the source", () => {
  const result = paginateEntries(entries);
  assert.equal(PAGE_SIZE, 10);
  assert.equal(result.page, 1);
  assert.equal(result.pageCount, 3);
  assert.equal(result.first, 1);
  assert.equal(result.last, 10);
  assert.deepEqual(result.entries, entries.slice(0, 10));
  assert.equal(entries.length, 25);
});

test("numbered pages cover every entry exactly once, including a partial last page", () => {
  const pages = [1, 2, 3].map((page) => paginateEntries(entries, page));
  assert.deepEqual(pages.flatMap((page) => page.entries), entries);
  assert.deepEqual(pages.map(({ first, last }) => [first, last]), [[1, 10], [11, 20], [21, 25]]);
});

test("pagination starts only above ten entries", () => {
  for (const count of [0, 1, 9, 10, 11, 20, 21]) {
    const result = paginateEntries(Array.from({ length: count }));
    assert.equal(result.pageCount, Math.max(1, Math.ceil(count / 10)));
    assert.equal(result.first, count ? 1 : 0);
    assert.equal(result.last, Math.min(count, 10));
  }
});

test("deletion clamps the last page to the remaining valid page", () => {
  const result = paginateEntries(entries.slice(0, 20), 3);
  assert.equal(result.page, 2);
  assert.deepEqual(result.entries, entries.slice(10, 20));
  const empty = paginateEntries([], 3);
  assert.equal(empty.page, 1);
  assert.deepEqual(empty.entries, []);
});

test("filtering happens before pagination", () => {
  const filtered = entries.filter(({ id }) => id % 2 === 1);
  assert.equal(paginateEntries(filtered).total, 13);
  assert.deepEqual(paginateEntries(filtered, 2).entries.map(({ id }) => id), [21, 23, 25]);
});

test("out-of-range page numbers are normalized", () => {
  for (const page of [-1, 0, NaN]) assert.equal(paginateEntries(entries, page).page, 1);
  assert.equal(paginateEntries(entries, 100).page, 3);
});

test("expanded property fragments stay together and nested lists paginate independently", () => {
  const properties = entries.map(({ id }) => createElement(
    Fragment, { key: id },
    createElement("tr", { key: "property" }),
    createElement("tr", { key: "units" }),
  ));
  const result = paginateEntries(Children.toArray(properties), 2);
  assert.equal(result.total, 25);
  assert.equal(result.entries.length, 10);
  for (const entry of result.entries) assert.equal(Children.count(entry.props.children), 2);
  const units = paginateEntries(entries, 3);
  assert.equal(units.entries.length, 5);
  assert.equal(result.page, 2);
});
