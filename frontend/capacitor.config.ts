import type { CapacitorConfig } from "@capacitor/cli";
import { KeyboardResize } from "@capacitor/keyboard";

const config: CapacitorConfig = {
  appId: "com.jpi.rental-mgmt",
  appName: "Rental Management",
  webDir: "dist-ios",
  loggingBehavior: "none",
  // Bundle the UI locally. Never load authenticated UI from a remote server.url.
  server: { hostname: "localhost", iosScheme: "capacitor" },
  ios: { contentInset: "never", preferredContentMode: "mobile", webContentsDebuggingEnabled: false },
  plugins: {
    Keyboard: { resize: KeyboardResize.Native },
    // Show reminders as banners even while the app is open.
    PushNotifications: { presentationOptions: ["alert", "sound"] },
  },
};

export default config;
