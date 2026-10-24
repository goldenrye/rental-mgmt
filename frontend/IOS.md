# iPhone Client

The iOS app packages the existing React client with Capacitor 8. It uses the
same FastAPI API, accounts, landlord isolation, encryption preferences, and
database as the website. No database migration is needed for this client.

## Included

- Properties and nested units, tenants, leases and lease details.
- Property bills, all recurrence types, filters, status changes, editing,
  deletion, extension previews and confirmations, and tenant-charge generation.
- Rental bills, payment entry and payment history.
- Profile, passwords, and opt-in data encryption.
- Super-admin landlord management, invitations, activation, and password resets.
- Phone-friendly labeled record cards, bottom navigation, 44-point touch
  targets, safe-area spacing, scrolling dialogs, and native keyboard resizing.
- Public website invitation links, native clipboard copying, and an invitation
  link/code entry on the iPhone login screen.
- Device-only Keychain token storage, scoped to the configured API URL.
  Native tokens are not persisted in browser localStorage or iCloud Keychain.
- A privacy cover for the app switcher's background snapshot.

The tenant portal retains the browser client's existing limited functionality;
this does not add a new tenant dashboard. This is an online client, not an
offline replica: no rental data is intentionally cached for offline use, and
failed writes are not queued or retried automatically. Existing admin
password-reset and encryption limitations remain unchanged.

## Prerequisites

1. Node.js 22 or newer.
2. Full Xcode 26 or newer, including an iOS simulator runtime. Command Line Tools
   alone cannot compile or run the app. Select Xcode in its Settings > Locations.
3. An HTTPS API reachable from the phone, with a publicly trusted certificate.
   A phone's `localhost` is the phone, not your Mac.
4. An Apple signing team for physical-device installation. TestFlight/App Store
   distribution requires Apple Developer Program membership and Apple review.

The generated project targets iOS 15+. Test on the oldest OS version you intend
to support as well as your current iPhone before release.

## Configure And Build

Run these commands from `frontend/`:

```bash
npm ci
cp .env.ios.example .env.ios.local
```

Set both URLs in `.env.ios.local` to your existing deployment:

```dotenv
VITE_API_BASE_URL=https://YOUR_DOMAIN/api
VITE_PUBLIC_APP_URL=https://YOUR_DOMAIN
```

`VITE_API_BASE_URL` is the API prefix without a trailing slash.
`VITE_PUBLIC_APP_URL` is the browser application's base URL, used for shareable
invitations. Both are public configuration, compiled into the app. Never put
passwords, JWT signing secrets, or encryption master keys in `VITE_*`.
The iOS build rejects missing URLs, relative URLs, HTTP, loopback hosts, embedded
credentials, query strings, and fragments.

Allow the bundled WebView origin on the API:

```dotenv
CORS_ORIGINS=https://YOUR_DOMAIN,capacitor://localhost
```

Retain any additional origins you already require. The production Compose
override includes `capacitor://localhost`; redeploy the API to apply it.
For a non-Compose deployment, update the server environment and restart the API.
This origin allowance does not replace bearer-token authentication.

Check the preflight response from your deployment:

```bash
curl -i -X OPTIONS https://YOUR_DOMAIN/api/me \
  -H 'Origin: capacitor://localhost' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
```

Expect HTTP 200 and `Access-Control-Allow-Origin: capacitor://localhost`.
Then build and open the native project:

```bash
npm run ios:sync
npm run ios:open
```

`ios:sync` type-checks, builds into `dist-ios/`, and copies the UI into
`ios/App/App/public`. The browser build continues to use `dist/`. Do not copy
a browser build over the native bundle, and do not set Capacitor `server.url`
to load a remote authenticated UI.

In Xcode:

1. Open the `App` target, then Signing & Capabilities.
2. Select your development team and set a unique bundle identifier you control.
   Keep `appId` in `capacitor.config.ts` consistent with the Xcode identifier.
3. Choose an iPhone simulator or connect an iPhone with Developer Mode enabled.
4. Run the `App` scheme.
5. For distribution, increment version/build numbers, archive, and upload through
   Organizer to App Store Connect. Finish privacy, export-compliance, and review
   requirements there. No signing identity or credentials are stored in this repo.

After every frontend change, run `npm run ios:sync` before building in Xcode.
The custom `SecureSession.swift` plugin is registered by
`RentalBridgeViewController`, used by both the scene delegate and storyboard.
Do not replace it with the default bridge controller: native sign-in intentionally
fails if secure storage is unavailable.

## Invitations

Admins create links pointing at `VITE_PUBLIC_APP_URL`, never
`capacitor://localhost`. Links still open registration on the website.
On the iPhone app, tap **Have an invitation?**, paste the link or code, and
register. Universal Links are not configured; they require deployment-specific
Apple entitlements and an `apple-app-site-association` file.

Automatic invitation email delivery is still not implemented.

## Testing

```bash
npm test
npx playwright install chromium webkit
npm run test:mobile
npm run build
npm run ios:sync
```

Browser tests use synthetic data and mocked API responses. They never write to
your real database. The suite covers desktop Chromium, iPhone-sized WebKit,
small phones, landscape, and a simulated Capacitor bridge. Test outputs go to
`/tmp/rental-mgmt-mobile-test-results`.

These tests do not replace a real native build or device testing. Before release:

- Verify Keychain persistence after force quitting, logout deletion, and behavior
  while the device is locked; no native token should appear in localStorage.
- Verify keyboard appearance, dismissal, rotation, notch/home-indicator spacing,
  date inputs, VoiceOver navigation, and the app-switcher privacy cover.
- Exercise each create/edit/delete action against a staging server, with two
  landlords to verify isolation, plus a super-admin account.
- Check payments, bill previews and confirmations, and both encryption modes.
- Test expired/revoked sessions, offline startup, and failed writes. Refresh after
  uncertain network failures before repeating a financial action.
- Confirm public invitation links, native clipboard, registration, and password
  changes on a physical iPhone.

## Validation In This Workspace

The frontend tests, browser production build, iOS asset synchronization, Swift
syntax parsing, and Xcode project/plist syntax checks were run. Native-bridge
tests simulate the plugin contract rather than executing Apple Keychain.

Full Xcode is not installed on the development machine used for this change.
No simulator build, device install, signed IPA, TestFlight upload, or App Store
release has been produced. The packaging smoke test used
`https://rental-test.example`; run `ios:sync` with your real URLs before opening
or distributing the project.
