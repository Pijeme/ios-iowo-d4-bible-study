@preconcurrency import UIKit
import WebKit
import Security

final class NativeServices {
    let installer: ResourceInstaller
    weak var controller: StudyViewController?
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    private var syncTimer: Timer?
    private let vaultService = "org.iowo.d4.biblestudy.device-session"

    init(installer: ResourceInstaller) { self.installer = installer }

    private func vaultQuery() -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: vaultService, kSecAttrAccount as String: "device-session"]
    }

    private func session() throws -> String {
        var query = vaultQuery(); query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let result = SecItemCopyMatching(query as CFDictionary, &value)
        if result == errSecItemNotFound { return "" }
        guard result == errSecSuccess, let data = value as? Data,
              let text = String(data: data, encoding: .utf8) else { throw NSError.study("Unlock the device to reconnect your saved sign-in.") }
        return text
    }

    private func saveSession(_ text: String) throws {
        guard let data = text.data(using: .utf8), data.count <= 65_536 else { throw NSError.study("Invalid device session.") }
        let query = vaultQuery()
        let update: [String: Any] = [kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var insert = query; update.forEach { insert[$0.key] = $0.value }
            guard SecItemAdd(insert as CFDictionary, nil) == errSecSuccess else { throw NSError.study("Could not securely remember this sign-in.") }
        } else if status != errSecSuccess { throw NSError.study("Could not securely remember this sign-in.") }
    }

    func dispatch(_ method: String, payload: String) -> String {
        do {
            let argument = try JSONSerialization.jsonObject(with: Data(payload.utf8), options: .fragmentsAllowed)
            let result: Any
            switch method {
            case "session.read": result = try session()
            case "session.write": try saveSession(argument as? String ?? ""); result = NSNull()
            case "resources.install": try installer.install(); result = NSNull()
            case "resources.progress": result = installer.progress()
            case "resources.required": result = try installer.requiredBytes()
            case "pdf.create":
                guard let note = argument as? String else { throw NSError.study("Invalid sermon export.") }
                result = try onMain { try SermonPDF.create(note).base64EncodedString() }
            case "sync.begin": onMain { beginSync() }; result = NSNull()
            default: throw NSError.study("Unsupported native service.")
            }
            return encode(["ok": true, "result": result])
        } catch { return encode(["ok": false, "error": error.localizedDescription]) }
    }

    private func encode(_ value: [String: Any]) -> String {
        String(data: (try? JSONSerialization.data(withJSONObject: value)) ?? Data("{\"ok\":false}".utf8), encoding: .utf8)!
    }

    private func onMain<T>(_ action: () throws -> T) rethrows -> T {
        if Thread.isMainThread { return try action() }
        return try DispatchQueue.main.sync(execute: action)
    }

    // iOS provides limited background time, unlike an Android foreground
    // service. Existing per-note sync request IDs remain the recovery mechanism.
    private func beginSync() {
        guard backgroundTask == .invalid else { return }
        backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "Sermon Notes backup") { [weak self] in self?.endSync() }
        syncTimer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            DispatchQueue.global(qos: .utility).async {
                var error: NSError?
                let text = IOWOEngineSyncStatus(&error) ?? "{}"
                let value = (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any]
                if value?["running"] as? Bool != true { DispatchQueue.main.async { self?.endSync() } }
            }
        }
    }

    private func endSync() {
        syncTimer?.invalidate(); syncTimer = nil
        if backgroundTask != .invalid { UIApplication.shared.endBackgroundTask(backgroundTask); backgroundTask = .invalid }
    }

    @MainActor func exportFile(_ args: [Any], presenter: UIViewController) throws {
        guard args.count == 4, let encoded = args[0] as? String, encoded.count <= 28 * 1_024 * 1_024,
              let type = args[2] as? String, ["application/pdf", "application/json"].contains(type),
              let data = Data(base64Encoded: encoded), data.count <= 20 * 1_024 * 1_024 else { throw NSError.study("This export is invalid or too large.") }
        let raw = (args[1] as? String ?? "sermon-notes.pdf")
        let filename = String(raw.map { $0.isASCII && ($0.isLetter || $0.isNumber || "._-".contains($0)) ? $0 : "_" }.prefix(160))
        guard !filename.isEmpty, filename != ".", filename != ".." else { throw NSError.study("Invalid export name.") }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let file = folder.appendingPathComponent(filename); try data.write(to: file, options: .atomic)
        if args[3] as? Bool == true {
            let share = UIActivityViewController(activityItems: [file], applicationActivities: nil)
            share.popoverPresentationController?.sourceView = presenter.view
            share.popoverPresentationController?.sourceRect = CGRect(x: presenter.view.bounds.midX, y: presenter.view.bounds.midY, width: 1, height: 1)
            presenter.present(share, animated: true)
        } else { presenter.present(UIDocumentPickerViewController(forExporting: [file], asCopy: true), animated: true) }
    }
}
