import { Capacitor, registerPlugin } from "@capacitor/core";
import { api } from "./api";
import type { PasskeyCreation, PasskeyRequest } from "./api";
import { isNative } from "./platform";

const nativePasskeys = registerPlugin<{
  isSupported(): Promise<{ supported: boolean }>;
  register(options: { publicKey: PasskeyCreation }): Promise<object>;
  authenticate(options: { publicKey: PasskeyRequest }): Promise<object>;
}>("Passkeys");

export async function supportsPasskeys() {
  if (isNative) {
    if (!Capacitor.isPluginAvailable("Passkeys")) return false;
    try { return (await nativePasskeys.isSupported()).supported; } catch { return false; }
  }
  return window.isSecureContext && typeof window.PublicKeyCredential !== "undefined" && !!navigator.credentials;
}

export function decodePasskeyBytes(value: string): ArrayBuffer {
  const text = value.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(text.padEnd(Math.ceil(text.length / 4) * 4, "=")), (char) => char.charCodeAt(0)).buffer;
}

function encode(value: ArrayBuffer) {
  return btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function serialize(credential: Credential | null, registration: boolean) {
  if (!credential || credential.type !== "public-key") throw new Error("Passkey request was cancelled. Try again or use your password.");
  const key = credential as PublicKeyCredential;
  const response = key.response;
  const common = { id: key.id, rawId: encode(key.rawId), type: "public-key", clientExtensionResults: key.getClientExtensionResults() };
  if (registration) {
    const attestation = response as AuthenticatorAttestationResponse;
    return { ...common, response: {
      clientDataJSON: encode(response.clientDataJSON), attestationObject: encode(attestation.attestationObject),
      transports: attestation.getTransports?.() ?? [],
    } };
  }
  const assertion = response as AuthenticatorAssertionResponse;
  return { ...common, response: {
    clientDataJSON: encode(response.clientDataJSON), authenticatorData: encode(assertion.authenticatorData),
    signature: encode(assertion.signature), userHandle: assertion.userHandle ? encode(assertion.userHandle) : null,
  } };
}

export function passkeyError(error: unknown) {
  if (error instanceof Error && ["NotAllowedError", "AbortError"].includes(error.name)) {
    return "Passkey request was cancelled. Try again or use your password.";
  }
  return error instanceof Error ? error.message : "Unable to use this passkey. Try again or use your password.";
}

export async function registerPasskey(password: string, name: string) {
  const options = await api.passkeyRegistrationOptions(password, name);
  const key = options.publicKey;
  const credential = isNative
    ? await nativePasskeys.register({ publicKey: key })
    : serialize(await navigator.credentials.create({ publicKey: {
      ...key, challenge: decodePasskeyBytes(key.challenge),
      user: { ...key.user, id: decodePasskeyBytes(key.user.id) },
      excludeCredentials: key.excludeCredentials?.map((item) => ({ ...item, id: decodePasskeyBytes(item.id) })),
    } }), true);
  await api.verifyPasskeyRegistration(options.ceremony_id, credential);
}

export async function loginWithPasskey() {
  try {
    const options = await api.passkeyLoginOptions();
    const key = options.publicKey;
    const credential = isNative
      ? await nativePasskeys.authenticate({ publicKey: key })
      : serialize(await navigator.credentials.get({ publicKey: {
        ...key, challenge: decodePasskeyBytes(key.challenge),
        allowCredentials: key.allowCredentials?.map((item) => ({ ...item, id: decodePasskeyBytes(item.id) })),
      } }), false);
    return await api.verifyPasskeyLogin(options.ceremony_id, credential);
  } catch (error) {
    throw new Error(passkeyError(error));
  }
}
