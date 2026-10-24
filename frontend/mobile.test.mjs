import assert from "node:assert/strict";
import test from "node:test";
import { createElement as h, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { clientConfig, invitationCode } from "./src/runtimeConfig.ts";
import { createTokenStorage } from "./src/tokenStorage.ts";
import { labelTableRows } from "./src/mobileTable.ts";

test("native configuration requires explicit reachable HTTPS URLs", () => {
  for (const url of [undefined, "/api", "http://example.com/api", "https://localhost/api", "https://127.0.0.1/api", "https://[::1]/api", "https://u:p@example.com", "https://example.com?token=x", "https://example.com/#invite=x"]) {
    assert.throws(() => clientConfig({ VITE_API_BASE_URL: url, VITE_PUBLIC_APP_URL: "https://rent.example" }, true));
    assert.throws(() => clientConfig({ VITE_API_BASE_URL: "https://rent.example/api", VITE_PUBLIC_APP_URL: url }, true));
  }
  assert.deepEqual(clientConfig({
    VITE_API_BASE_URL: "https://rent.example/api/",
    VITE_PUBLIC_APP_URL: "https://rent.example/",
  }, true), { apiBaseURL: "https://rent.example/api", publicAppURL: "https://rent.example" });
});

test("browser configuration preserves relative API proxy and browser invitation origin", () => {
  assert.deepEqual(clientConfig({ VITE_API_BASE_URL: "/api" }, false, "https://rent.example"),
    { apiBaseURL: "/api", publicAppURL: "https://rent.example" });
});

test("invitation entry extracts fragment without following the supplied URL", () => {
  const code = "abc_DEF-12345678901234567890";
  assert.equal(invitationCode(`https://rent.example/#invite=${code}`), code);
  assert.equal(invitationCode(` ${code} `), code);
  for (const invalid of ["", "https://rent.example/", "short", "https://rent.example/?invite=not-in-fragment"]) {
    assert.throws(() => invitationCode(invalid));
  }
});

function browserStorage() {
  const entries = new Map();
  return {
    getItem: (key) => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value),
    removeItem: (key) => entries.delete(key),
  };
}

test("browser sessions preserve existing token storage", async () => {
  const browser = browserStorage();
  browser.setItem("rental_mgmt_token", "existing");
  const session = createTokenStorage(browser);
  await session.initialize();
  assert.equal(session.get(), "existing");
  await session.set("new");
  assert.equal(browser.getItem("rental_mgmt_token"), "new");
  await session.clear();
  assert.equal(session.get(), null);
});

test("native sessions use secure storage only and survive app restarts", async () => {
  const browser = browserStorage();
  browser.setItem("rental_mgmt_token", "browser-token");
  let token = "keychain-token";
  const secure = {
    getToken: async () => ({ token }),
    setToken: async (options) => { token = options.token; },
    removeToken: async () => { token = null; },
  };
  const session = createTokenStorage(browser, secure);
  assert.throws(() => session.get(), /initialized/);
  await session.initialize();
  assert.equal(session.get(), "keychain-token");
  assert.equal(browser.getItem("rental_mgmt_token"), null);
  await session.set("replacement");
  const restarted = createTokenStorage(browser, secure);
  await restarted.initialize();
  assert.equal(restarted.get(), "replacement");
  assert.equal(browser.getItem("rental_mgmt_token"), null);
  await restarted.clear();
  assert.equal(token, null);
  assert.equal(restarted.get(), null);
});

test("native secure storage failures do not fall back to browser storage", async () => {
  const browser = browserStorage();
  const failure = async () => { throw new Error("locked"); };
  const session = createTokenStorage(browser, { getToken: failure, setToken: failure, removeToken: failure });
  await assert.rejects(session.initialize(), /locked/);
  assert.throws(() => session.get(), /initialized/);
  await assert.rejects(session.set("secret"), /locked/);
  assert.equal(browser.getItem("rental_mgmt_token"), null);
  await assert.rejects(session.clear(), /locked/);
});

test("mobile table labels preserve fragments, hidden unit rows, and nested headers", () => {
  const rows = h(Fragment, { key: "property" },
    h("tr", { onClick() {} }, h("td", null, "Home"), h("td", null, "Street"), h("td", null, h("button", null, "Edit"))),
    h("tr", { hidden: true, className: "property-units-row" },
      h("td", { colSpan: 3 }, h("table", null, h("tbody", null, h("tr", null, h("td", { "data-label": "Unit" }, "Suite")))))),
  );
  const markup = renderToStaticMarkup(h("table", null, h("tbody", null, labelTableRows(rows, ["Property", "Address", "Actions"]))));
  assert.match(markup, /data-label="Property"/);
  assert.match(markup, /data-label="Address"/);
  assert.match(markup, /data-label="Actions" data-full-width="true"/);
  assert.match(markup, /hidden=""/);
  assert.match(markup, /colSpan="3" role="cell" data-full-width="true"/);
  assert.match(markup, /data-label="Unit"/);
});
