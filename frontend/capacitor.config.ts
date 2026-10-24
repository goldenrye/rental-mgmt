import type { CapacitorConfig } from "@capacitor/cli";
import { KeyboardResize } from "@capacitor/keyboard";

const config: CapacitorConfig = {
  appId: "com.rentalmgmt.app",
  appName: "Rental Management",
  webDir: "dist-ios",
  loggingBehavior: "none",
  // Bundle the UI locally. Never load authenticated UI from a remote server.url.
  server: { hostname: "localhost", iosScheme: "capacitor" },
  ios: { contentInset: "never", preferredContentMode: "mobile", webContentsDebuggingEnabled: false },
  plugins: {
    Keyboard: { resize: KeyboardResize.Native },
  },
};

export default config;
