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
- Optional Face ID / Touch ID protection on launch and return from the background,
  with device-passcode fallback.
- Opt-in lease-ending and bill-due reminders through Apple Push Notification
  service, turned on in **Edit Profile > Notifications**. Banners show while the
  app is open too, and contain only counts and dates.

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
It also registers the `@capacitor/push-notifications` plugin in the generated
`capacitor.config.json`; without a sync the notification button fails.
The custom `SecureSession.swift` plugin is registered by
`RentalBridgeViewController`, used by both the scene delegate and storyboard.
Do not replace it with the default bridge controller: native sign-in intentionally
fails if secure storage is unavailable.

## Face ID And Touch ID

Sign in, then open **Edit Profile > Biometric Unlock** and turn on
**Require Face ID or Touch ID on this device**. Super-admins have the same
control on their dashboard. Protection is off by default and applies only to
this device and configured API, not other devices or browser sessions.

Set up Face ID or Touch ID in iPhone Settings first. Enabling or disabling
protection requires device authentication. Once enabled:

- Cold launch authenticates before loading the saved session from Keychain.
- Returning from the background shows an opaque native lock screen until
  authentication succeeds. Canceling keeps the app locked; tap **Unlock** to retry.
- On canceled cold-start authentication, the secure-storage screen offers
  **Try Again**, without loading the dashboard.
- The device passcode is an alternative when biometric authentication fails.
- Signing out removes the saved session but remembers the biometric preference
  for this device and API. Signed-out screens do not lock. The next sign-in
  stores the session with the remembered protection, even for a different account.
  Turn the switch off explicitly to disable it. Expired or revoked sessions still
  require account sign-in; Face ID does not extend them.

The feature stores a session token protected by Apple's Keychain `userPresence`
access control, never an account password or biometric data. Tokens stay on this
device and are not synced to iCloud. Native authentication prompts follow the
device language; the settings panel follows the selected app language.
Anyone able to authenticate with this device's biometrics or passcode can unlock
the saved account.

This is a native change. Run `ios:sync`, rebuild in Xcode, and install the new
app. Deploying the website alone does not update the installed iPhone app.
If Capacitor cannot read its TypeScript configuration on Node 22, use
`NODE_OPTIONS=--experimental-strip-types npm run ios:sync` and the same prefix
for `npm run ios:open`.

For passwordless sign-in, see **Passkey Sign-In** below. Biometric Unlock protects
an existing session; it does not register a passkey.

## Notifications

The app target has the **Push Notifications** capability (`App/App.entitlements`,
`aps-environment`). Xcode signs debug builds for the APNs sandbox and switches to
production when you archive for TestFlight or the App Store. With automatic
signing, enable Push Notifications on the App ID once, under Signing &
Capabilities or in the Apple Developer portal.

The API needs an APNs key. See "iPhone app (APNs) setup" in the main README.
Without one, the settings panel says notifications are not configured on the
server. Remote notifications do not arrive in the iOS Simulator from this server.
Use a physical iPhone and check that:

- **Turn On for This Device** shows the iOS permission prompt once, and
  **Send Test Notification** arrives with the app in the foreground and background.
- Disabling notifications in iOS Settings, then opening Edit Profile, removes the
  device from the server.
- Signing out removes the device. Signing in as another landlord and turning
  notifications on moves the device to that landlord.

## Passkey Sign-In

Native passkeys use Apple's AuthenticationServices on iOS 16+, not WebAuthn from
the `capacitor://localhost` WebView. The custom `Passkeys.swift` plugin is registered
alongside `SecureSession`; it returns signed proofs to the backend. Only the
backend can issue the new session token, which still goes into device Keychain.
Users on iOS 15 can continue using passwords and the optional biometric app lock.

Complete the [server passkey setup](../README.md#passkey-sign-in), then:

1. Set `PASSKEY_APPLE_APP_IDS` on the backend to the signed app's application
   identifier, such as `ABCDE12345.com.jpi.rental-mgmt`. The App ID prefix is
   usually the Apple Team ID, but check your provisioning profile rather than
   assuming they match.
2. In Xcode, select the **App** target > **Signing & Capabilities** and add
   **Associated Domains**. Add `webcredentials:rent.example.com`, using exactly
   your configured RP hostname, without a scheme, path, or `/api`.
   Xcode adds `com.apple.developer.associated-domains` to `App/App.entitlements`.
   Enable the capability for your App ID and refresh the provisioning profile.
3. Verify `https://rent.example.com/.well-known/apple-app-site-association`
   returns HTTP 200 JSON with `webcredentials.apps` containing that exact app
   identifier, without authentication or redirects. The included nginx configs
   route this root URL to the API. A custom reverse proxy/CDN must do the same.
4. Run `npm run ios:sync`, rebuild in Xcode, and reinstall the app. Deploying the
   website alone cannot install the native plugin or signing entitlement.
5. Enable iCloud Keychain (or a supported passkey provider), sign in, open
   **Edit Profile > Passkeys**, confirm your account password, and create a passkey.
   Sign out and use **Sign in with a passkey** to test a fresh Face ID login.

Verify the hostname in `App/App.entitlements` matches the deployed RP ID.
Until the signing capability and association above are configured, native passkey
requests will fail and password login remains available. Apple caches domain
associations; check the deployed file and reinstall after fixing configuration.
Only `webcredentials` is served; this does not enable Universal Links.

### Disabled Create Button

The current-password field takes your rental-mgmt **login password**, not the
iPhone passcode. It confirms enrollment or removal; it cannot enable server support.
The form now gives a specific reason when setup is unavailable and offers
**Retry Passkey Setup**, which preserves your name/password draft.

For `rental-mgmt.us`, the server's deployment `.env` must include:

```dotenv
PASSKEY_RP_ID=rental-mgmt.us
PASSKEY_ORIGINS=https://rental-mgmt.us
# Verify the App ID prefix against the signed app's application-identifier entitlement.
PASSKEY_APPLE_APP_IDS=2U5R5BXJ37.com.jpi.rental-mgmt
```

Recreate the API container after changing its environment; `docker compose restart`
does not load changed Compose environment values:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build api frontend
curl https://rental-mgmt.us/api/auth/passkeys/status
curl https://rental-mgmt.us/.well-known/apple-app-site-association
```

Expect `available: true` with RP ID `rental-mgmt.us`, and an association containing
the signed app's identifier. Then retry the setup check in the app. If the message
instead reports a missing native plugin, sync/rebuild/reinstall the iPhone app.

Before release, test creation, logout/login, cancellation, device-passcode
fallback, account selection, removal, password recovery, and background/return
with the biometric app lock enabled. Verify the same passkey can sign in on the
associated website when shared through iCloud Keychain. iOS versions before 17.4
do not support the native exclusion list; the server still rejects a credential
already registered to an account.

The implementation's full simulator build was attempted, but this environment
blocked Swift package resolution with `sandbox-exec: sandbox_apply: Operation
not permitted`, before compilation. Swift syntax/project checks and mocked bridge
tests are not a substitute for that build and real-device testing.

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
- Test biometric enable/disable on an existing saved session, cold launch,
  background/return, repeated cancellation and retry, passcode fallback, biometric
  lockout, removed enrollment, and logout followed by a different account.
  Verify rental details cannot be seen or interacted with behind the lock,
  including with VoiceOver. Also test backgrounding during an authentication prompt.
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

The Face ID simulator build was attempted with Xcode, but dependency resolution
was blocked by `sandbox-exec: sandbox_apply: Operation not permitted` before
Swift compilation. Biometric bridge tests use mocked authentication and do not
verify Apple's prompts, Keychain access-control migration, or native lifecycle
behavior. Those still require an unrestricted Xcode build and simulator/physical
device checks before release.

No device install, signed IPA, TestFlight upload, or App Store release has been
produced here. The packaging smoke test used
`https://rental-test.example`; run `ios:sync` with your real URLs before opening
or distributing the project.
