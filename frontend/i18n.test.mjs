import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { createElement as h, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { detectLanguage, getLanguage, setLanguage, subscribeLanguage, t, languageStorageKey } from "./src/i18n.ts";
import { translations } from "./src/translations.ts";
import { labelTableRows } from "./src/mobileTable.ts";

test("saved language wins; browser preferences fall back to a supported language", () => {
  assert.equal(detectLanguage("en", ["zh-CN"]), "en");
  assert.equal(detectLanguage("zh-CN", ["en-US"]), "zh-CN");
  assert.equal(detectLanguage(null, ["zh-Hans", "en"]), "zh-CN");
  assert.equal(detectLanguage(null, ["zh-TW"]), "zh-CN");
  assert.equal(detectLanguage(null, ["en-GB", "zh-CN"]), "en");
  assert.equal(detectLanguage("invalid", ["fr-FR", "zh-CN"]), "zh-CN");
  assert.equal(detectLanguage(null, ["fr-FR"]), "en");
  assert.equal(detectLanguage(null, []), "en");
});

test("language changes notify subscribers, persist, and interpolate without altering values", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const values = new Map();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    setItem: (key, value) => values.set(key, value),
  } });
  let calls = 0;
  const unsubscribe = subscribeLanguage(() => calls++);
  try {
    setLanguage("zh-CN");
    assert.equal(getLanguage(), "zh-CN");
    assert.equal(values.get(languageStorageKey), "zh-CN");
    assert.equal(t("Monthly Rent"), "月租金");
    assert.equal(t("Welcome, {{name}}", { name: "Properties 李 & <Chen>" }), "欢迎，Properties 李 & <Chen>");
    assert.equal(t("Outstanding balance: ${{balance}}", { balance: "1000.00" }), "待付余额：$1000.00");
    assert.equal(t("{{count}} units", { count: 2 }), "2 个单元");
    assert.equal(t("Unknown future message"), "Unknown future message");
    setLanguage("en");
    assert.equal(t("Monthly Rent"), "Monthly Rent");
    assert.equal(t("Welcome, {{name}}", { name: "李" }), "Welcome, 李");
    assert.equal(calls, 2);
  } finally {
    unsubscribe();
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
    setLanguage("en");
  }
});

test("blocked preference storage does not prevent in-memory language switching", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("Blocked"); } });
  try {
    assert.doesNotThrow(() => setLanguage("zh-CN"));
    assert.equal(t("Login"), "登录");
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
    setLanguage("en");
  }
});

test("all translations preserve interpolation variables", () => {
  const variables = (value) => [...value.matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]).sort();
  for (const [key, value] of Object.entries(translations)) {
    assert.ok(value.trim(), key);
    assert.deepEqual(variables(value), variables(key), key);
  }
});

test("literal translation calls and table headers have catalog entries", () => {
  const keys = new Set();
  const directory = new URL("./src/", import.meta.url);
  for (const filename of readdirSync(directory).filter((name) => name.endsWith(".tsx"))) {
    const source = readFileSync(new URL(filename, directory), "utf8");
    for (const match of source.matchAll(/\bt\(("(?:\\.|[^"\\])*")/g)) keys.add(JSON.parse(match[1]));
    for (const match of source.matchAll(/(?:label|empty)=("(?:\\.|[^"\\])*")/g)) keys.add(JSON.parse(match[1]));
    for (const match of source.matchAll(/headers=\{(\[[^\]]*\])/g)) {
      for (const key of JSON.parse(match[1])) keys.add(key);
    }
  }
  assert.deepEqual([...keys].filter((key) => !translations[key]), []);
});

test("translated mobile labels keep action cells full-width and preserve entered content", () => {
  const rows = h(Fragment, null, h("tr", null,
    h("td", null, "Properties"), h("td", null, "123 Street"), h("td", null, h("button", null, "编辑"))));
  const markup = renderToStaticMarkup(h("table", null, h("tbody", null,
    labelTableRows(rows, ["房产", "地址", "操作"], ["Property", "Address", "Actions"]))));
  assert.match(markup, /data-label="操作" data-full-width="true"/);
  assert.match(markup, />Properties<\/td>/);
  assert.match(markup, />123 Street<\/td>/);
});
