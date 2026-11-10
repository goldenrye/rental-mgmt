import AuthenticationServices
import Capacitor
import Foundation
import UIKit

@objc(PasskeysPlugin)
public class PasskeysPlugin: CAPPlugin, CAPBridgedPlugin, ASAuthorizationControllerDelegate,
    ASAuthorizationControllerPresentationContextProviding {
    public let identifier = "PasskeysPlugin"
    public let jsName = "Passkeys"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isSupported", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "register", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "authenticate", returnType: CAPPluginReturnPromise)
    ]
    private var pending: CAPPluginCall?
    private var controller: ASAuthorizationController?
    private var anchor: UIWindow?

    @objc func isSupported(_ call: CAPPluginCall) {
        if #available(iOS 16.0, *) { call.resolve(["supported": true]) }
        else { call.resolve(["supported": false]) }
    }

    @objc func register(_ call: CAPPluginCall) {
        DispatchQueue.main.async { self.begin(call, registration: true) }
    }

    @objc func authenticate(_ call: CAPPluginCall) {
        DispatchQueue.main.async { self.begin(call, registration: false) }
    }

    private func decode(_ value: String?) -> Data? {
        guard let value = value, value.count <= 4096 else { return nil }
        var base64 = value.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        base64 += String(repeating: "=", count: (4 - base64.count % 4) % 4)
        return Data(base64Encoded: base64)
    }

    private func encode(_ value: Data) -> String {
        value.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }

    private func begin(_ call: CAPPluginCall, registration: Bool) {
        guard #available(iOS 16.0, *) else {
            call.reject("Passkeys require iOS 16 or later.")
            return
        }
        guard pending == nil, !SessionSecurity.authenticationInProgress else {
            call.reject("Authentication is already in progress.")
            return
        }
        guard let window = bridge?.viewController?.view.window,
              window.windowScene?.activationState == .foregroundActive,
              let options = call.getObject("publicKey"),
              let challenge = decode(options["challenge"] as? String), !challenge.isEmpty else {
            call.reject("Unable to start passkey authentication.")
            return
        }
        let rp = registration ? (options["rp"] as? [String: Any])?["id"] as? String : options["rpId"] as? String
        guard let rp = rp, !rp.isEmpty else {
            call.reject("Unable to start passkey authentication.")
            return
        }
        let provider = ASAuthorizationPlatformPublicKeyCredentialProvider(relyingPartyIdentifier: rp)
        let request: ASAuthorizationRequest
        if registration {
            guard let user = options["user"] as? [String: Any],
                  let userID = decode(user["id"] as? String), let name = user["name"] as? String else {
                call.reject("Unable to start passkey authentication.")
                return
            }
            let creation = provider.createCredentialRegistrationRequest(challenge: challenge, name: name, userID: userID)
            creation.displayName = user["displayName"] as? String
            creation.userVerificationPreference = .required
            creation.attestationPreference = .none
            if #available(iOS 17.4, *) {
                let excluded = options["excludeCredentials"] as? [[String: Any]] ?? []
                creation.excludedCredentials = excluded.compactMap { item in
                    guard let id = decode(item["id"] as? String) else { return nil }
                    return ASAuthorizationPlatformPublicKeyCredentialDescriptor(credentialID: id)
                }
            }
            request = creation
        } else {
            let assertion = provider.createCredentialAssertionRequest(challenge: challenge)
            assertion.userVerificationPreference = .required
            request = assertion
        }
        pending = call
        anchor = window
        SessionSecurity.authenticationInProgress = true
        let controller = ASAuthorizationController(authorizationRequests: [request])
        self.controller = controller
        controller.delegate = self
        controller.presentationContextProvider = self
        controller.performRequests()
    }

    public func presentationAnchor(for controller: ASAuthorizationController) -> ASPresentationAnchor {
        anchor ?? ASPresentationAnchor()
    }

    public func authorizationController(controller: ASAuthorizationController, didCompleteWithAuthorization authorization: ASAuthorization) {
        var response: [String: Any]
        let id: Data
        if let credential = authorization.credential as? ASAuthorizationPlatformPublicKeyCredentialRegistration,
           let attestation = credential.rawAttestationObject {
            id = credential.credentialID
            response = ["clientDataJSON": encode(credential.rawClientDataJSON),
                        "attestationObject": encode(attestation), "transports": ["internal"]]
        } else if let credential = authorization.credential as? ASAuthorizationPlatformPublicKeyCredentialAssertion {
            id = credential.credentialID
            response = ["clientDataJSON": encode(credential.rawClientDataJSON),
                        "authenticatorData": encode(credential.rawAuthenticatorData),
                        "signature": encode(credential.signature), "userHandle": encode(credential.userID)]
        } else {
            finish(error: "Unable to use this passkey. Try again or use your password.")
            return
        }
        let call = pending
        clear()
        call?.resolve(["id": encode(id), "rawId": encode(id), "type": "public-key",
                       "response": response, "clientExtensionResults": [:]])
    }

    public func authorizationController(controller: ASAuthorizationController, didCompleteWithError error: Error) {
        let cancelled = (error as? ASAuthorizationError)?.code == .canceled
        finish(error: cancelled
               ? "Passkey request was cancelled. Try again or use your password."
               : "Unable to use this passkey. Check iCloud Keychain and the app's associated domain, or use your password.")
    }

    private func finish(error: String) {
        let call = pending
        clear()
        call?.reject(error)
    }

    private func clear() {
        pending = nil
        controller = nil
        anchor = nil
        SessionSecurity.authenticationInProgress = false
    }
}
