import Capacitor
import Foundation
import LocalAuthentication
import Security
import UIKit

enum SessionSecurity {
    private struct StoredSession: Codable {
        let token: String
        let biometric: Bool
    }
    static let queue = DispatchQueue(label: "com.rentalmgmt.session", qos: .userInitiated)
    // Accessed on the main thread; the setting itself is scoped to the API in UserDefaults.
    static var activeServer: String?
    static var authenticationInProgress = false
    static var service: String { "\(Bundle.main.bundleIdentifier ?? "com.rentalmgmt.app").session" }
    static var reason: String { text("Unlock Rental Management", "解锁租赁管理") }

    static func text(_ english: String, _ chinese: String) -> String {
        Locale.preferredLanguages.first?.hasPrefix("zh") == true ? chinese : english
    }

    static func query(_ server: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: server,
            kSecAttrSynchronizable as String: false
        ]
    }

    static func enabled(_ server: String) -> Bool {
        UserDefaults.standard.bool(forKey: "\(service).biometric.\(server)")
    }

    static func setEnabled(_ value: Bool, server: String) {
        UserDefaults.standard.set(value, forKey: "\(service).biometric.\(server)")
    }

    static func read(_ server: String, context: LAContext = LAContext()) -> (OSStatus, String?) {
        var attributes = query(server)
        context.localizedReason = reason
        attributes[kSecUseAuthenticationContext as String] = context
        attributes[kSecReturnData as String] = true
        attributes[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(attributes as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return (status, nil) }
        if let saved = try? JSONDecoder().decode(StoredSession.self, from: data) {
            // Restore the setting from the protected item if app preferences were cleared.
            setEnabled(saved.biometric, server: server)
            return (status, saved.token)
        }
        // Existing installations stored only the token, without biometric protection.
        setEnabled(false, server: server)
        return (status, String(data: data, encoding: .utf8))
    }

    static func write(_ token: String, server: String, protected: Bool, context: LAContext = LAContext()) -> OSStatus {
        // userPresence requires Face ID / Touch ID or the device passcode, and never syncs to iCloud.
        guard let access = SecAccessControlCreateWithFlags(
            nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, protected ? .userPresence : [], nil
        ), let data = try? JSONEncoder().encode(StoredSession(token: token, biometric: protected))
        else { return errSecParam }
        var attributes = query(server)
        attributes[kSecUseAuthenticationContext as String] = context
        let changes: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessControl as String: access
        ]
        let status = SecItemUpdate(attributes as CFDictionary, changes as CFDictionary)
        if status == errSecItemNotFound {
            return SecItemAdd(attributes.merging(changes) { _, new in new } as CFDictionary, nil)
        }
        return status
    }
}

class RentalBridgeViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(SecureSessionPlugin())
    }

    override var preferredStatusBarStyle: UIStatusBarStyle { .darkContent }
}

@objc(SecureSessionPlugin)
public class SecureSessionPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "SecureSessionPlugin"
    public let jsName = "SecureSession"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "getToken", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setToken", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "removeToken", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getBiometricStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setBiometricEnabled", returnType: CAPPluginReturnPromise)
    ]

    private func server(_ call: CAPPluginCall) -> String? {
        guard let server = call.getString("server"),
              let url = URL(string: server), url.scheme == "https", url.host != nil else {
            call.reject("A valid HTTPS server is required.")
            return nil
        }
        SessionSecurity.activeServer = server
        return server
    }

    @objc func getToken(_ call: CAPPluginCall) {
        // Capacitor invokes plugins on its bridge queue; lifecycle state belongs on main.
        DispatchQueue.main.async { self.loadToken(call) }
    }

    private func loadToken(_ call: CAPPluginCall) {
        guard let server = server(call) else { return }
        SessionSecurity.queue.async {
            let (status, token) = SessionSecurity.read(server)
            if status == errSecItemNotFound {
                SessionSecurity.setEnabled(false, server: server)
                call.resolve(["token": NSNull()])
            } else if status == errSecSuccess, let token = token {
                call.resolve(["token": token])
            } else {
                call.reject("Unable to read the secure session.", String(status))
            }
        }
    }

    @objc func setToken(_ call: CAPPluginCall) {
        DispatchQueue.main.async { self.storeToken(call) }
    }

    private func storeToken(_ call: CAPPluginCall) {
        guard let server = server(call) else { return }
        guard let token = call.getString("token"), !token.isEmpty else {
            call.reject("A nonempty session token is required.")
            return
        }
        SessionSecurity.queue.async {
            let status = SessionSecurity.write(token, server: server, protected: SessionSecurity.enabled(server))
            if status == errSecSuccess { call.resolve() }
            else { call.reject("Unable to store the secure session.", String(status)) }
        }
    }

    @objc func removeToken(_ call: CAPPluginCall) {
        DispatchQueue.main.async { self.deleteToken(call) }
    }

    private func deleteToken(_ call: CAPPluginCall) {
        guard let server = server(call) else { return }
        SessionSecurity.queue.async {
            let status = SecItemDelete(SessionSecurity.query(server) as CFDictionary)
            if status == errSecSuccess || status == errSecItemNotFound {
                SessionSecurity.setEnabled(false, server: server)
                call.resolve()
            } else {
                call.reject("Unable to remove the secure session.", String(status))
            }
        }
    }

    private func biometricStatus(_ server: String) -> JSObject {
        let context = LAContext()
        let available = context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil)
        return [
            "available": available,
            "enabled": SessionSecurity.enabled(server),
            "type": context.biometryType == .faceID ? "face" : context.biometryType == .touchID ? "touch" : "none"
        ]
    }

    @objc func getBiometricStatus(_ call: CAPPluginCall) {
        DispatchQueue.main.async { self.loadBiometricStatus(call) }
    }

    private func loadBiometricStatus(_ call: CAPPluginCall) {
        guard let server = server(call) else { return }
        call.resolve(biometricStatus(server))
    }

    @objc func setBiometricEnabled(_ call: CAPPluginCall) {
        DispatchQueue.main.async { self.updateBiometricEnabled(call) }
    }

    private func updateBiometricEnabled(_ call: CAPPluginCall) {
        guard let server = server(call) else { return }
        guard let enabled = call.getBool("enabled") else {
            call.reject("A biometric preference is required.")
            return
        }
        guard !SessionSecurity.authenticationInProgress else {
            call.reject("Authentication is already in progress.")
            return
        }
        let context = LAContext()
        if enabled && !context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: nil) {
            call.reject("Set up Face ID or Touch ID in iPhone Settings first.")
            return
        }
        SessionSecurity.authenticationInProgress = true
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: SessionSecurity.reason) { success, _ in
            guard success else {
                DispatchQueue.main.async { SessionSecurity.authenticationInProgress = false }
                call.reject("Authentication was cancelled or failed. No settings were changed.")
                return
            }
            SessionSecurity.queue.async {
                let (readStatus, token) = SessionSecurity.read(server, context: context)
                guard readStatus == errSecSuccess, let token = token else {
                    DispatchQueue.main.async { SessionSecurity.authenticationInProgress = false }
                    call.reject("Unable to read the secure session. Sign in again.")
                    return
                }
                let status = SessionSecurity.write(token, server: server, protected: enabled, context: context)
                if status == errSecSuccess {
                    SessionSecurity.setEnabled(enabled, server: server)
                }
                DispatchQueue.main.async {
                    SessionSecurity.authenticationInProgress = false
                    if status == errSecSuccess { call.resolve(self.biometricStatus(server)) }
                    else { call.reject("Unable to update biometric protection.", String(status)) }
                }
            }
        }
    }
}
