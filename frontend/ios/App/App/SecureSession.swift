import Capacitor
import Foundation
import Security
import UIKit

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
        CAPPluginMethod(name: "removeToken", returnType: CAPPluginReturnPromise)
    ]

    private func query(_ call: CAPPluginCall) -> [String: Any]? {
        guard let server = call.getString("server"),
              let url = URL(string: server), url.scheme == "https", url.host != nil else {
            call.reject("A valid HTTPS server is required.")
            return nil
        }
        // Separate sessions across deployment environments sharing this bundle ID.
        return [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "\(Bundle.main.bundleIdentifier ?? "com.rentalmgmt.app").session",
            kSecAttrAccount as String: server,
            kSecAttrSynchronizable as String: false
        ]
    }

    @objc func getToken(_ call: CAPPluginCall) {
        guard var attributes = query(call) else { return }
        attributes[kSecReturnData as String] = true
        attributes[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(attributes as CFDictionary, &result)
        if status == errSecItemNotFound {
            call.resolve(["token": NSNull()])
        } else if status == errSecSuccess, let data = result as? Data,
                  let token = String(data: data, encoding: .utf8) {
            call.resolve(["token": token])
        } else {
            call.reject("Unable to read the secure session.", String(status))
        }
    }

    @objc func setToken(_ call: CAPPluginCall) {
        guard let attributes = query(call) else { return }
        guard let token = call.getString("token"), !token.isEmpty else {
            call.reject("A nonempty session token is required.")
            return
        }
        let changes: [String: Any] = [
            kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        ]
        var status = SecItemUpdate(attributes as CFDictionary, changes as CFDictionary)
        if status == errSecItemNotFound {
            let item = attributes.merging(changes) { _, new in new }
            status = SecItemAdd(item as CFDictionary, nil)
        }
        if status == errSecSuccess {
            call.resolve()
        } else {
            call.reject("Unable to store the secure session.", String(status))
        }
    }

    @objc func removeToken(_ call: CAPPluginCall) {
        guard let attributes = query(call) else { return }
        let status = SecItemDelete(attributes as CFDictionary)
        if status == errSecSuccess || status == errSecItemNotFound {
            call.resolve()
        } else {
            call.reject("Unable to remove the secure session.", String(status))
        }
    }
}
