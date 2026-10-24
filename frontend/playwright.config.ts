import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "/tmp/rental-mgmt-mobile-test-results",
  reporter: "list",
  use: { baseURL: "http://127.0.0.1:5179", trace: "off" },
  projects: [
    { name: "iphone-webkit", testMatch: "mobile.spec.ts", use: { ...devices["iPhone 13"] } },
    { name: "iphone-small", testMatch: "mobile.spec.ts", use: { ...devices["iPhone SE"] } },
    { name: "iphone-landscape", testMatch: "mobile.spec.ts", use: { ...devices["iPhone 13 landscape"] } },
    { name: "desktop-chromium", testMatch: "mobile.spec.ts", use: { ...devices["Desktop Chrome"] } },
    { name: "native-bridge-webkit", testMatch: "native.spec.ts", use: { ...devices["iPhone 13"], baseURL: "http://127.0.0.1:5180" } },
  ],
  webServer: [{
    command: "npm run dev -- --host 127.0.0.1 --port 5179 --strictPort",
    url: "http://127.0.0.1:5179",
    reuseExistingServer: false,
    env: { VITE_API_BASE_URL: "/api" },
  }, {
    command: "npm run dev -- --mode ios --host 127.0.0.1 --port 5180 --strictPort",
    url: "http://127.0.0.1:5180",
    reuseExistingServer: false,
    env: {
      VITE_API_BASE_URL: "https://rental-test.example/api",
      VITE_PUBLIC_APP_URL: "https://rental-test.example",
    },
  }],
});
