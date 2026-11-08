import UIKit
import Capacitor
import LocalAuthentication
import Security

class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    private var privacyCover: UIView?
    private var unlockButton: UIButton?
    private var lockMessage: UILabel?
    private var unlockContext: LAContext?
    private var attemptID = UUID()
    private var automaticallyAttempted = false
    private var authenticatedOnReturn = false

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }

        window = UIWindow(windowScene: windowScene)
        window?.rootViewController = RentalBridgeViewController()
        window?.overrideUserInterfaceStyle = .light
        window?.makeKeyAndVisible()

        SceneDelegateProxy.shared.scene(scene, willConnectTo: session, options: connectionOptions)
    }

    func sceneDidEnterBackground(_ scene: UIScene) {
        automaticallyAttempted = false
        authenticatedOnReturn = false
        attemptID = UUID()
        unlockContext?.invalidate()
        unlockContext = nil
        window?.endEditing(true)
        window?.rootViewController?.view.isUserInteractionEnabled = false
        guard let window = window, privacyCover == nil else { return }
        // Keep rental details out of the app switcher's background snapshot.
        let cover = UIView(frame: window.bounds)
        cover.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        cover.backgroundColor = .systemBackground
        let label = UILabel(frame: cover.bounds)
        label.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        label.text = "Rental Management"
        label.textAlignment = .center
        cover.addSubview(label)
        window.addSubview(cover)
        privacyCover = cover
    }

    func sceneWillEnterForeground(_ scene: UIScene) {
        if let server = SessionSecurity.activeServer, SessionSecurity.enabled(server) {
            showLockControls()
        } else {
            removeCover()
        }
    }

    func sceneDidBecomeActive(_ scene: UIScene) {
        guard privacyCover != nil else { return }
        // LocalAuthentication can complete just before UIKit marks the scene active.
        if authenticatedOnReturn {
            removeCover()
            return
        }
        if let server = SessionSecurity.activeServer, SessionSecurity.enabled(server) {
            showLockControls()
            if !automaticallyAttempted {
                automaticallyAttempted = true
                unlock()
            }
        } else {
            removeCover()
        }
    }

    private func removeCover() {
        authenticatedOnReturn = false
        window?.rootViewController?.view.isUserInteractionEnabled = true
        privacyCover?.removeFromSuperview()
        privacyCover = nil
        unlockButton = nil
        lockMessage = nil
    }

    private func showLockControls() {
        guard let cover = privacyCover else { return }
        if let button = unlockButton {
            button.isEnabled = unlockContext == nil
            return
        }
        cover.subviews.forEach { $0.removeFromSuperview() }
        cover.accessibilityViewIsModal = true
        let message = UILabel()
        message.text = SessionSecurity.text("Rental Management is locked", "租赁管理已锁定")
        message.font = .preferredFont(forTextStyle: .headline)
        message.numberOfLines = 0
        message.textAlignment = .center
        let button = UIButton(type: .system)
        button.setTitle(SessionSecurity.text("Unlock", "解锁"), for: .normal)
        button.titleLabel?.font = .preferredFont(forTextStyle: .headline)
        button.addTarget(self, action: #selector(unlock), for: .touchUpInside)
        button.heightAnchor.constraint(greaterThanOrEqualToConstant: 48).isActive = true
        let stack = UIStackView(arrangedSubviews: [message, button])
        stack.axis = .vertical
        stack.spacing = 20
        stack.translatesAutoresizingMaskIntoConstraints = false
        cover.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerYAnchor.constraint(equalTo: cover.centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: cover.safeAreaLayoutGuide.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: cover.safeAreaLayoutGuide.trailingAnchor, constant: -24)
        ])
        lockMessage = message
        unlockButton = button
        UIAccessibility.post(notification: .screenChanged, argument: message)
    }

    @objc private func unlock() {
        guard let server = SessionSecurity.activeServer, unlockContext == nil,
              !SessionSecurity.authenticationInProgress,
              window?.windowScene?.activationState == .foregroundActive else { return }
        let context = LAContext()
        let currentAttempt = UUID()
        attemptID = currentAttempt
        unlockContext = context
        unlockButton?.isEnabled = false
        context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: SessionSecurity.reason) { success, _ in
            // Verify the protected item as well; never pass its contents back to the web view on resume.
            SessionSecurity.queue.async {
                let valid = success && SessionSecurity.read(server, context: context).0 == errSecSuccess
                DispatchQueue.main.async {
                    guard self.attemptID == currentAttempt else { return }
                    self.unlockContext = nil
                    self.unlockButton?.isEnabled = true
                    if valid {
                        self.authenticatedOnReturn = true
                        if self.window?.windowScene?.activationState == .foregroundActive {
                            self.removeCover()
                        }
                    } else {
                        self.lockMessage?.text = SessionSecurity.text(
                            "Authentication was cancelled or failed. Tap Unlock to try again.",
                            "认证已取消或失败，请轻点“解锁”重试。"
                        )
                    }
                }
            }
        }
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        SceneDelegateProxy.shared.scene(scene, openURLContexts: URLContexts)
    }

    func scene(_ scene: UIScene, continue userActivity: NSUserActivity) {
        SceneDelegateProxy.shared.scene(scene, continue: userActivity)
    }
}
