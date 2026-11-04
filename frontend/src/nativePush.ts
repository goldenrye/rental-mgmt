import { PushNotifications } from "@capacitor/push-notifications";
import { api } from "./api";

// The APNs token is not a credential (sending also needs the server's Apple key); it is kept so
// sign-out and "turn off" can remove this install even if registration is unavailable then.
const tokenKey = "rental-mgmt.native-push-token";

function storedToken() {
  try {
    return window.localStorage.getItem(tokenKey);
  } catch {
    return null;
  }
}

function storeToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(tokenKey, token);
    else window.localStorage.removeItem(tokenKey);
  } catch {
    // The server copy still works; only automatic re-sync needs this.
  }
}

export function nativePushEnabledHere() {
  return Boolean(storedToken());
}

export async function nativePushPermission() {
  return (await PushNotifications.checkPermissions()).receive;
}

// register() reports the token through events, so wait for whichever arrives first.
async function deviceToken() {
  const listeners: Array<{ remove: () => Promise<void> }> = [];
  try {
    return await new Promise<string>((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error("The iPhone did not finish registering for notifications. Check your connection and try again.")), 20000);
      const done = () => window.clearTimeout(timer);
      Promise.all([
        PushNotifications.addListener("registration", ({ value }) => { done(); resolve(value); }),
        PushNotifications.addListener("registrationError", () => {
          done();
          reject(new Error("The iPhone could not register for notifications. Try again later."));
        }),
      ]).then((handles) => {
        listeners.push(...handles);
        return PushNotifications.register();
      }).catch((error) => { done(); reject(error); });
    });
  } finally {
    await Promise.all(listeners.map((listener) => listener.remove()));
  }
}

export async function enableNativePush() {
  let permission = await nativePushPermission();
  if (permission === "prompt" || permission === "prompt-with-rationale") {
    permission = (await PushNotifications.requestPermissions()).receive;
  }
  if (permission !== "granted") {
    throw new Error("Notifications are turned off for this app. Allow them in Settings > Notifications > Rental Management, then try again.");
  }
  return syncNativePush();
}

// Re-register on each visit: APNs tokens can change, and the server moves them between sign-ins.
export async function syncNativePush() {
  const token = await deviceToken();
  const previous = storedToken();
  const status = await api.addNativePushDevice(token);
  if (previous && previous !== token) await api.removeNativePushDevice(previous).catch(() => {});
  storeToken(token);
  return status;
}

export async function disableNativePush() {
  const token = storedToken();
  if (token) await api.removeNativePushDevice(token);
  storeToken(null);
  await PushNotifications.unregister().catch(() => {});
}
