import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

test("native language choice survives reload without changing secure session storage", async ({ page }) => {
  const bridge = await nativeBridge(page);
  await page.goto("/");
  await signIn(page);
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByLabel("Language", { exact: true }).selectOption("zh-CN");
  await expect(page.getByRole("heading", { name: "欢迎，Native User" })).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
  await expect(page.getByRole("heading", { name: "欢迎，Native User" })).toBeVisible();
  expect(bridge.getToken()).toBe("native-test-token");
  expect(await page.evaluate(() => localStorage.getItem("rental_mgmt_token"))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem("rental_mgmt_language"))).toBe("zh-CN");
  await page.getByRole("button", { name: "打开个人菜单" }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "登录", exact: true })).toBeVisible();
  expect(bridge.getToken()).toBeNull();
});

// Exercises the JS/native contract, not Apple's Keychain implementation.
async function nativeBridge(page: Page, options: {
  locked?: boolean; admin?: boolean; biometricAvailable?: boolean;
} = {}) {
  let token: string | null = null;
  let biometricEnabled = false;
  let rejectAuthentication = false;
  const biometricStatus = () => ({
    available: options.biometricAvailable ?? true,
    enabled: biometricEnabled,
    type: options.biometricAvailable === false ? "none" : "face",
  });
  let pushPermission = "prompt";
  const calls: { plugin: string; method: string; options: Record<string, string | boolean> }[] = [];
  await page.exposeFunction("testNativeCall", async (plugin: string, method: string, args: Record<string, string | boolean>) => {
    calls.push({ plugin, method, options: args });
    if (plugin === "SecureSession") {
      expect(args.server).toBe("https://rental-test.example/api");
      if (options.locked) throw new Error("Device locked");
      if (method === "getToken") {
        if (biometricEnabled && rejectAuthentication) throw new Error("Authentication cancelled");
        return { token };
      }
      if (method === "setToken") token = String(args.token);
      if (method === "removeToken") {
        token = null;
        biometricEnabled = false;
      }
      if (method === "getBiometricStatus") return biometricStatus();
      if (method === "setBiometricEnabled") {
        if (rejectAuthentication) throw new Error("Authentication was cancelled or failed. No settings were changed.");
        expect(token).not.toBeNull();
        biometricEnabled = args.enabled === true;
        return biometricStatus();
      }
    }
    if (plugin === "PushNotifications") {
      if (method === "requestPermissions") pushPermission = "granted";
      if (method === "checkPermissions" || method === "requestPermissions") return { receive: pushPermission };
    }
    return {};
  });
  await page.addInitScript(() => {
    const pushListeners = new Map<string, (data: unknown) => void>();
    Object.assign(window, {
      CapacitorCustomPlatform: { name: "ios" },
      Capacitor: {
        PluginHeaders: [
          { name: "SecureSession", methods: ["getToken", "setToken", "removeToken", "getBiometricStatus", "setBiometricEnabled"].map((name) => ({ name, rtype: "promise" })) },
          { name: "Keyboard", methods: [{ name: "addListener", rtype: "callback" }, { name: "removeListener", rtype: "callback" }] },
          { name: "Clipboard", methods: [{ name: "write", rtype: "promise" }] },
          { name: "PushNotifications", methods: [
            ...["register", "unregister", "checkPermissions", "requestPermissions"].map((name) => ({ name, rtype: "promise" })),
            { name: "addListener", rtype: "callback" }, { name: "removeListener", rtype: "callback" },
          ] },
        ],
        nativePromise: async (plugin: string, method: string, args: Record<string, string>) => {
          const result = await Reflect.get(window, "testNativeCall")(plugin, method, args);
          // APNs answers register() with a "registration" event carrying the device token.
          if (plugin === "PushNotifications" && method === "register") {
            setTimeout(() => pushListeners.get("registration")?.({ value: "ab".repeat(32) }));
          }
          return result;
        },
        nativeCallback: (plugin: string, method: string, args: Record<string, string>, callback: (data: unknown) => void) => {
          if (plugin === "PushNotifications" && method === "addListener") pushListeners.set(args.eventName, callback);
          if (plugin === "PushNotifications" && method === "removeListener") pushListeners.delete(args.eventName);
          return "listener";
        },
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
  return {
    calls, getToken: () => token, biometricStatus,
    rejectAuthentication: (reject: boolean) => { rejectAuthentication = reject; },
  };
}

async function signIn(page: Page) {
  await page.getByLabel("Email or admin username").fill("test@example.test");
  await page.getByLabel("Password", { exact: true }).fill("native-password");
  await page.getByRole("button", { name: "Login", exact: true }).click();
}

async function openProfile(page: Page) {
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Edit Profile", exact: true }).click();
}

test("biometric protection is opt-in, persists across launch, and resets on logout", async ({ page }) => {
  const bridge = await nativeBridge(page);
  await page.goto("/");
  await signIn(page);
  await openProfile(page);
  const toggle = page.getByRole("checkbox", { name: "Require Face ID or Touch ID on this device" });
  await expect(toggle).toBeEnabled();
  await expect(toggle).not.toBeChecked();
  await toggle.click();
  await expect(toggle).toBeChecked();
  expect(bridge.biometricStatus().enabled).toBe(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Welcome, Native User" })).toBeVisible();
  await openProfile(page);
  await expect(toggle).toBeChecked();
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await toggle.click();
  await expect(toggle).toBeChecked();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Logout", exact: true }).click();
  await expect(page.getByRole("button", { name: "Login", exact: true })).toBeVisible();
  expect(bridge.biometricStatus().enabled).toBe(false);
  expect(bridge.getToken()).toBeNull();
  expect(bridge.calls.filter((call) => call.method === "setBiometricEnabled").map((call) => call.options.enabled))
    .toEqual([true, false, true]);
});

test("cancelled biometric changes preserve the setting and protected startup fails closed", async ({ page }) => {
  const bridge = await nativeBridge(page);
  await page.goto("/");
  await signIn(page);
  await openProfile(page);
  const toggle = page.getByRole("checkbox", { name: "Require Face ID or Touch ID on this device" });
  bridge.rejectAuthentication(true);
  await toggle.click();
  await expect(page.getByRole("alert")).toContainText("No settings were changed");
  await expect(toggle).not.toBeChecked();
  bridge.rejectAuthentication(false);
  await toggle.click();
  await expect(toggle).toBeChecked();
  bridge.rejectAuthentication(true);
  await toggle.click();
  await expect(page.getByRole("alert")).toContainText("No settings were changed");
  await expect(toggle).toBeChecked();
  let authenticatedRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/me") authenticatedRequests += 1;
  });
  await page.reload();
  await expect(page.getByRole("heading", { name: "Unable to open secure storage" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Welcome, Native User" })).toHaveCount(0);
  expect(authenticatedRequests).toBe(0);
  expect(await page.evaluate(() => localStorage.getItem("rental_mgmt_token"))).toBeNull();
  bridge.rejectAuthentication(false);
  await page.getByRole("button", { name: "Try Again" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Native User" })).toBeVisible();
});

test("unconfigured biometrics cannot be enabled", async ({ page }) => {
  const bridge = await nativeBridge(page, { biometricAvailable: false });
  await page.goto("/");
  await signIn(page);
  await openProfile(page);
  await expect(page.getByRole("checkbox", { name: "Require Face ID or Touch ID on this device" })).toBeDisabled();
  await expect(page.getByText("Set up Face ID or Touch ID in iPhone Settings first.")).toBeVisible();
  expect(bridge.calls.some((call) => call.method === "setBiometricEnabled")).toBe(false);
});

test("native admins can enable biometric protection", async ({ page }) => {
  await nativeBridge(page, { admin: true });
  await page.goto("/");
  await signIn(page);
  const toggle = page.getByRole("checkbox", { name: "Require Face ID or Touch ID on this device" });
  await expect(toggle).not.toBeChecked();
  await toggle.click();
  await expect(toggle).toBeChecked();
});

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

test("native calendar settings use website authorization and keep exports opt-in", async ({ page }, testInfo) => {
  await nativeBridge(page);
  let connected = false;
  let enabled = false;
  await page.route("https://rental-test.example/api/me/google-calendar", (route) => {
    if (route.request().method() === "PUT") enabled = route.request().postDataJSON().enabled;
    return route.fulfill({ json: {
      available: true, enabled, connected, calendar_name: connected ? "Rental Due Dates" : null,
      pending_exports: 0, last_synced_at: null, last_error: null,
    } });
  });
  await page.goto("/");
  await signIn(page);
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Edit Profile", exact: true }).click();
  await expect(page.getByRole("button", { name: "Connect Google Calendar", exact: true })).toBeDisabled();
  await expect(page.getByRole("link", { name: "rental website" })).toHaveAttribute("href", "https://rental-test.example");
  const toggle = page.getByRole("checkbox", { name: "Automatically sync new rental bills to Google Calendar" });
  await expect(toggle).toBeDisabled();
  connected = true;
  await page.getByRole("button", { name: "Refresh Calendar Status" }).click();
  await expect(toggle).toBeEnabled();
  await expect(toggle).not.toBeChecked();
  page.on("dialog", (dialog) => dialog.accept());
  await toggle.click();
  await expect(toggle).toBeChecked();
  await page.getByRole("heading", { name: "Google Calendar: On" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("google-calendar-settings.png") });
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

test("native notifications register the APNs token, re-sync on launch, and are removed on logout", async ({ page }) => {
  const bridge = await nativeBridge(page);
  const devices = new Set<string>();
  const deleted: string[] = [];
  const status = () => ({
    available: false, native_available: true, public_key: null, devices: devices.size,
    lease_expiry: true, bill_due: true, lease_notice_days: 30, bill_notice_days: 3,
  });
  await page.route("https://rental-test.example/api/me/web-push", (route) => route.fulfill({ json: status() }));
  await page.route("https://rental-test.example/api/me/native-push/devices", (route) => {
    const { token } = route.request().postDataJSON();
    if (route.request().method() === "DELETE") {
      deleted.push(token);
      devices.delete(token);
      return route.fulfill({ status: 204 });
    }
    expect(route.request().postDataJSON().platform).toBe("ios");
    devices.add(token);
    return route.fulfill({ json: status() });
  });
  await page.goto("/");
  await signIn(page);
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Edit Profile", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Notifications: Off for this device" })).toBeVisible();
  await expect(page.getByText("not available in the iPhone app")).toHaveCount(0);
  await page.getByRole("button", { name: "Turn On for This Device" }).click();
  await expect(page.getByText("Notifications are on for this device.")).toBeVisible();
  expect([...devices]).toEqual(["ab".repeat(32)]);
  expect(bridge.calls.filter((call) => call.plugin === "PushNotifications").map((call) => call.method))
    .toEqual(["checkPermissions", "requestPermissions", "register"]);

  devices.clear();
  await page.reload();
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Edit Profile", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Notifications: On for this device" })).toBeVisible();
  expect(devices.size).toBe(1);

  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Open profile menu" }).click();
  await page.getByRole("button", { name: "Logout", exact: true }).click();
  await expect(page.getByRole("button", { name: "Login", exact: true })).toBeVisible();
  expect(deleted).toEqual(["ab".repeat(32)]);
  expect(bridge.calls.some((call) => call.plugin === "PushNotifications" && call.method === "unregister")).toBe(true);
  expect(await page.evaluate(() => localStorage.getItem("rental-mgmt.native-push-token"))).toBeNull();
});
