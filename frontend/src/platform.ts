import { Capacitor, registerPlugin } from "@capacitor/core";
import { Clipboard } from "@capacitor/clipboard";
import { Keyboard } from "@capacitor/keyboard";
import { clientConfig } from "./runtimeConfig";
import { createTokenStorage } from "./tokenStorage";

export const isNative = Capacitor.isNativePlatform();
export const runtimeConfig = clientConfig({
  VITE_API_BASE_URL: import.meta.env.VITE_API_BASE_URL,
  VITE_PUBLIC_APP_URL: import.meta.env.VITE_PUBLIC_APP_URL,
}, isNative, window.location.origin);
const secureSession = registerPlugin<{
  getToken(options: { server: string }): Promise<{ token: string | null }>;
  setToken(options: { server: string; token: string }): Promise<void>;
  removeToken(options: { server: string }): Promise<void>;
}>("SecureSession");
export const sessionStorage = createTokenStorage(
  window.localStorage,
  isNative ? {
    getToken: () => secureSession.getToken({ server: runtimeConfig.apiBaseURL }),
    setToken: ({ token }) => secureSession.setToken({ server: runtimeConfig.apiBaseURL, token }),
    removeToken: () => secureSession.removeToken({ server: runtimeConfig.apiBaseURL }),
  } : undefined,
);

export async function initializePlatform() {
  await sessionStorage.initialize();
  if (isNative) {
    document.documentElement.classList.add("native-app");
    await Keyboard.addListener("keyboardWillShow", () => document.documentElement.classList.add("keyboard-open"));
    await Keyboard.addListener("keyboardWillHide", () => document.documentElement.classList.remove("keyboard-open"));
  }
}

export async function copyText(text: string) {
  if (isNative) await Clipboard.write({ string: text });
  else await navigator.clipboard.writeText(text);
}
