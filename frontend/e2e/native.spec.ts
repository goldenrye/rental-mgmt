import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

// Exercises the JS/native contract, not Apple's Keychain implementation.
async function nativeBridge(page: Page, options: { locked?: boolean; admin?: boolean } = {}) {
  let token: string | null = null;
  const calls: { plugin: string; method: string; options: Record<string, string> }[] = [];
  await page.exposeFunction("testNativeCall", async (plugin: string, method: string, args: Record<string, string>) => {
    calls.push({ plugin, method, options: args });
    if (plugin === "SecureSession") {
      expect(args.server).toBe("https://rental-test.example/api");
      if (options.locked) throw new Error("Device locked");
      if (method === "getToken") return { token };
      if (method === "setToken") token = args.token;
      if (method === "removeToken") token = null;
    }
    return {};
  });
  await page.addInitScript(() => {
    Object.assign(window, {
      CapacitorCustomPlatform: { name: "ios" },
      Capacitor: {
        PluginHeaders: [
          { name: "SecureSession", methods: ["getToken", "setToken", "removeToken"].map((name) => ({ name, rtype: "promise" })) },
          { name: "Keyboard", methods: [{ name: "addListener", rtype: "callback" }, { name: "removeListener", rtype: "callback" }] },
          { name: "Clipboard", methods: [{ name: "write", rtype: "promise" }] },
        ],
        nativePromise: (plugin: string, method: string, args: Record<string, string>) =>
          Reflect.get(window, "testNativeCall")(plugin, method, args),
        nativeCallback: () => "listener",
      },
    });
  });
  await page.route("https://rental-test.example/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/login") return route.fulfill({ json: { access_token: "native-test-token", token_type: "bearer" } });
    if (path === "/api/auth/register") return route.fulfill({ json: {} });
    if (path === "/api/auth/logout") return route.fulfill({ status: 204 });
    if (path === "/api/me") {
      expect(route.request().headers().authorization).toBe("Bearer native-test-token");
      return route.fulfill({ json: {
        id: 1, email: "test@example.test", full_name: "Native User", role: options.admin ? "super_admin" : "landlord",
        password_change_required: false, is_active: true, encryption_enabled: false,
      } });
    }
    if (path === "/api/admin/invites" && route.request().method() === "POST") {
      return route.fulfill({ json: { id: 1, code: "native-invitation-1234567890", email: "new@example.test", status: "active" } });
    }
    return route.fulfill({ json: [] });
  });
  return { calls, getToken: () => token };
}

async function signIn(page: Page) {
  await page.getByLabel("Email or admin username").fill("test@example.test");
  await page.getByLabel("Password", { exact: true }).fill("native-password");
  await page.getByRole("button", { name: "Login", exact: true }).click();
}

test("native login, cold start, and logout use only the secure session bridge", async ({ page }) => {
  const bridge = await nativeBridge(page);
  await page.goto("/");
  await signIn(page);
  await expect(page.getByRole("heading", { name: "Welcome, Native User" })).toBeVisible();
  expect(bridge.getToken()).toBe("native-test-token");
  expect(await page.evaluate(() => localStorage.getItem("rental_mgmt_token"))).toBeNull();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Welcome, Native User" })).toBeVisible();
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Logout", exact: true }).click();
  await expect(page.getByRole("button", { name: "Login", exact: true })).toBeVisible();
  expect(bridge.getToken()).toBeNull();
  expect(bridge.calls.filter((call) => call.method === "setToken")).toHaveLength(1);
  expect(bridge.calls.some((call) => call.method === "removeToken")).toBe(true);
});

test("native secure storage fails closed", async ({ page }) => {
  await nativeBridge(page, { locked: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Unable to open secure storage" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Login", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.getItem("rental_mgmt_token"))).toBeNull();
});

test("native registration accepts the existing website invitation", async ({ page }) => {
  await nativeBridge(page);
  await page.goto("/");
  await page.getByText("Have an invitation?", { exact: true }).click();
  await page.getByLabel("Invitation link or code").fill("https://rental-test.example/#invite=existing-invitation-1234567890");
  await page.getByRole("button", { name: "Use Invitation" }).click();
  await page.getByLabel("Full name", { exact: true }).fill("Native User");
  await page.getByLabel("Email", { exact: true }).fill("test@example.test");
  await page.getByLabel("Password", { exact: true }).fill("native-password");
  const registration = page.waitForRequest("https://rental-test.example/api/auth/register");
  await page.getByRole("button", { name: "Create Account", exact: true }).click();
  expect((await registration).postDataJSON().invite_code).toBe("existing-invitation-1234567890");
  await expect(page.getByRole("heading", { name: "Welcome, Native User" })).toBeVisible();
});

test("native admin invitations use public HTTPS links and the clipboard plugin", async ({ page }) => {
  const bridge = await nativeBridge(page, { admin: true });
  await page.goto("/");
  await signIn(page);
  await page.getByLabel("Landlord email (optional)").fill("new@example.test");
  await page.getByRole("button", { name: "Create Invite Link" }).click();
  const link = "https://rental-test.example/#invite=native-invitation-1234567890";
  await expect(page.getByLabel("Invitation link", { exact: true })).toHaveValue(link);
  await page.getByRole("button", { name: "Copy Link" }).click();
  await expect(page.getByRole("button", { name: "Copied", exact: true })).toBeVisible();
  expect(bridge.calls.find((call) => call.plugin === "Clipboard")?.options.string).toBe(link);
});
