export type ClientEnvironment = {
  VITE_API_BASE_URL?: string;
  VITE_PUBLIC_APP_URL?: string;
};

function secureURL(value: string | undefined, name: string): string {
  if (!value) throw new Error(`${name} is required for the iOS build.`);
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${name} must be an absolute HTTPS URL.`);
  }
  if (
    url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
    ["localhost", "127.0.0.1", "[::1]", "0.0.0.0"].includes(url.hostname) ||
    url.hostname.endsWith(".localhost")
  ) {
    throw new Error(`${name} must be an HTTPS server reachable by the iPhone, without credentials, query, or fragment.`);
  }
  return url.toString().replace(/\/+$/, "");
}

export function clientConfig(env: ClientEnvironment, native: boolean, browserOrigin = "") {
  if (native) {
    return {
      apiBaseURL: secureURL(env.VITE_API_BASE_URL, "VITE_API_BASE_URL"),
      publicAppURL: secureURL(env.VITE_PUBLIC_APP_URL, "VITE_PUBLIC_APP_URL"),
    };
  }
  return {
    apiBaseURL: (env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, ""),
    publicAppURL: (env.VITE_PUBLIC_APP_URL || browserOrigin).replace(/\/+$/, ""),
  };
}

export function invitationCode(value: string): string {
  const input = value.trim();
  if (!input) throw new Error("Paste your invitation link or code.");
  const code = input.includes("://")
    ? new URLSearchParams(new URL(input).hash.slice(1)).get("invite")
    : input;
  if (!code || !/^[A-Za-z0-9_-]{20,256}$/.test(code)) {
    throw new Error("This is not a valid invitation link or code.");
  }
  return code;
}
