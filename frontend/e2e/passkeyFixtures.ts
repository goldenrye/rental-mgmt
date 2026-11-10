import { expect } from "@playwright/test";
import type { Page } from "@playwright/test";

// UI/bridge contract fixture only. Backend tests verify real cryptographic proofs.
export async function mockPasskeyAPI(page: Page, token: string) {
  let registered = false;
  const calls: string[] = [];
  await page.route("**/api/**passkeys**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    calls.push(path);
    if (path.endsWith("/status")) return route.fulfill({ json: { available: true, rp_id: "rental-test.example" } });
    if (path.endsWith("/me/passkeys")) return route.fulfill({ json: registered
      ? [{ id: "passkey-1", name: "My iPhone", created_at: "2026-11-10T12:00:00Z", last_used_at: null }] : [] });
    if (route.request().method() === "DELETE") {
      expect(route.request().postDataJSON()).toEqual({ current_password: "test-password" });
      registered = false;
      return route.fulfill({ status: 204 });
    }
    if (path.endsWith("/register/options")) {
      expect(route.request().postDataJSON()).toEqual({ current_password: "test-password", name: "My iPhone" });
      return route.fulfill({ json: { ceremony_id: "registration-ceremony", publicKey: {
        challenge: "AQID", rp: { id: "rental-test.example", name: "Rental Management" },
        user: { id: "BAUG", name: "owner@example.com", displayName: "Owner" },
        pubKeyCredParams: [{ type: "public-key", alg: -7 }],
        authenticatorSelection: { residentKey: "required", userVerification: "required" },
        excludeCredentials: [], attestation: "none",
      } } });
    }
    if (path.endsWith("/register/verify")) {
      const body = route.request().postDataJSON();
      expect(body.ceremony_id).toBe("registration-ceremony");
      expect(body.credential.response.attestationObject).toBe("BwgJ");
      registered = true;
      return route.fulfill({ status: 201, json: { id: "passkey-1" } });
    }
    if (path.endsWith("/options")) return route.fulfill({ json: { ceremony_id: "login-ceremony",
      publicKey: { challenge: "AQID", rpId: "rental-test.example", userVerification: "required" } } });
    if (path.endsWith("/verify")) {
      const body = route.request().postDataJSON();
      expect(body.ceremony_id).toBe("login-ceremony");
      expect(body.credential.response.signature).toBe("BwgJ");
      expect(registered).toBe(true);
      return route.fulfill({ json: { access_token: token, token_type: "bearer" } });
    }
    throw new Error("Unexpected passkey path: " + path);
  });
  return calls;
}

export async function mockBrowserAuthenticator(page: Page) {
  await page.addInitScript(() => {
    Object.defineProperty(window, "PublicKeyCredential", { value: class {}, configurable: true });
    const bytes = (value: number[]) => new Uint8Array(value).buffer;
    const credential = (registration: boolean, options: CredentialCreationOptions | CredentialRequestOptions) => {
      if (Reflect.get(window, "cancelPasskey")) throw new DOMException("Cancelled", "NotAllowedError");
      const key = options.publicKey!;
      if (!(key.challenge instanceof ArrayBuffer) || new Uint8Array(key.challenge)[2] !== 3) throw new Error("Challenge was not decoded");
      if (registration) {
        const create = key as PublicKeyCredentialCreationOptions;
        if (!(create.user.id instanceof ArrayBuffer) || new Uint8Array(create.user.id)[2] !== 6) throw new Error("User ID was not decoded");
      }
      return {
        id: "AQID", rawId: bytes([1, 2, 3]), type: "public-key", getClientExtensionResults: () => ({}),
        response: registration
          ? { clientDataJSON: bytes([1, 2, 3]), attestationObject: bytes([7, 8, 9]), getTransports: () => ["internal"] }
          : { clientDataJSON: bytes([1, 2, 3]), authenticatorData: bytes([4, 5, 6]), signature: bytes([7, 8, 9]), userHandle: bytes([4, 5, 6]) },
      };
    };
    Object.defineProperty(navigator, "credentials", { configurable: true, value: {
      create: async (options: CredentialCreationOptions) => credential(true, options),
      get: async (options: CredentialRequestOptions) => credential(false, options),
    } });
  });
}

export async function createPasskeyInProfile(page: Page) {
  await page.getByLabel("Passkey name", { exact: true }).fill("My iPhone");
  await page.getByLabel("Current password to manage passkeys").fill("test-password");
  await page.getByRole("button", { name: "Create a Passkey", exact: true }).click();
  await expect(page.getByText("Passkey registered. You can use it on the sign-in page.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Current password to manage passkeys")).toHaveValue("");
}
