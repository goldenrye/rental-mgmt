export interface SecureTokenStorage {
  getToken(): Promise<{ token: string | null }>;
  setToken(options: { token: string }): Promise<void>;
  removeToken(): Promise<void>;
}

const TOKEN_KEY = "rental_mgmt_token";

export function createTokenStorage(browser: Storage, secure?: SecureTokenStorage) {
  let nativeToken: string | null = null;
  let initialized = false;

  return {
    async initialize() {
      if (secure) {
        // Never migrate a browser token into the native security boundary.
        browser.removeItem(TOKEN_KEY);
        nativeToken = (await secure.getToken()).token;
      }
      initialized = true;
    },
    get(): string | null {
      if (!initialized) throw new Error("Session storage has not initialized.");
      return secure ? nativeToken : browser.getItem(TOKEN_KEY);
    },
    async set(token: string) {
      if (secure) {
        await secure.setToken({ token });
        nativeToken = token;
      } else {
        browser.setItem(TOKEN_KEY, token);
      }
    },
    async clear() {
      // Forget the in-memory session even if the device refuses Keychain access.
      nativeToken = null;
      if (secure) await secure.removeToken();
      else browser.removeItem(TOKEN_KEY);
    },
  };
}
