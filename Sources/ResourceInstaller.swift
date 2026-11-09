import Foundation
import CryptoKit

struct ResourceEntry: Decodable {
    let path: String
    let bytes: Int64
    let sha256: String
}
struct ResourceManifest: Decodable {
    let files: [ResourceEntry]
    let resource_bytes: Int64
}

final class ResourceInstaller {
    let bundle: URL
    let data: URL
    let manifest: ResourceManifest
    private let lock = NSLock()
    private let installLock = NSLock()
    private var completed = 0
    private var message = "Preparing offline study resources"

    init() throws {
        bundle = Bundle.main.resourceURL!.appendingPathComponent("Resources", isDirectory: true)
        data = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                          appropriateFor: nil, create: true).appendingPathComponent("study-data", isDirectory: true)
        try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
        manifest = try JSONDecoder().decode(ResourceManifest.self,
                      from: Data(contentsOf: bundle.appendingPathComponent("resource-manifest.json")))
        // Content is protected at rest and remains available during an authorized
        // background sync after the device has first been unlocked.
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                                              ofItemAtPath: data.path)
    }

    func destination(_ path: String) throws -> URL {
        let base = data.standardizedFileURL
        let result = base.appendingPathComponent(path).standardizedFileURL
        guard result.path.hasPrefix(base.path + "/"), !path.contains("\\") else {
            throw NSError.study("Invalid offline resource location.")
        }
        return result
    }

    func hash(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url); defer { try? handle.close() }
        var digest = SHA256()
        while let bytes = try handle.read(upToCount: 1_048_576), !bytes.isEmpty { digest.update(data: bytes) }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }

    func requiredBytes() throws -> Int64 {
        var required: Int64 = 268_435_456
        for entry in manifest.files {
            let file = try destination(entry.path)
            let size = (try? file.resourceValues(forKeys: [.fileSizeKey]).fileSize).map(Int64.init)
            if size != entry.bytes { required += entry.bytes }
        }
        return required
    }

    func progress() -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        return ["completed": completed, "total": manifest.files.count, "message": message]
    }

    func install() throws {
        installLock.lock(); defer { installLock.unlock() }
        let capacity = try data.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        guard (capacity.volumeAvailableCapacityForImportantUsage ?? 0) >= (try requiredBytes()) else {
            throw NSError.study("Free some storage and retry. Your notes were kept.")
        }
        for (index, entry) in manifest.files.enumerated() {
            let target = try destination(entry.path)
            if FileManager.default.fileExists(atPath: target.path) {
                guard try hash(target) == entry.sha256 else {
                    throw NSError.study("An existing library needs recovery before setup. Your saved data was kept.")
                }
            } else {
                try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
                let staged = target.appendingPathExtension("installing")
                // Discard only our unfinished staging file, never an existing database.
                if FileManager.default.fileExists(atPath: staged.path) { try FileManager.default.removeItem(at: staged) }
                guard let input = InputStream(url: bundle.appendingPathComponent(entry.path)),
                      let output = OutputStream(url: staged, append: false) else {
                    throw NSError.study("The bundled library could not be opened.")
                }
                input.open(); output.open()
                defer { input.close(); output.close() }
                var buffer = [UInt8](repeating: 0, count: 1_048_576)
                while true {
                    let count = input.read(&buffer, maxLength: buffer.count)
                    if count == 0 { break }
                    if count < 0 { throw input.streamError ?? NSError.study("Library read failed.") }
                    try buffer.withUnsafeBufferPointer { bytes in
                        var offset = 0
                        while offset < count {
                            let written = output.write(bytes.baseAddress!.advanced(by: offset), maxLength: count - offset)
                            guard written > 0 else { throw output.streamError ?? NSError.study("Library write failed.") }
                            offset += written
                        }
                    }
                }
                output.close(); input.close()
                guard try hash(staged) == entry.sha256 else { throw NSError.study("A bundled resource failed its integrity check.") }
                try FileManager.default.moveItem(at: staged, to: target)
            }
            // Only replaceable reference resources are excluded from iCloud
            // backup. Personal notes and the writable reader with its marks
            // keep the normal user-data backup behavior.
            if entry.path.hasPrefix("library/") || entry.path == "study/sermon_illustrations.db" {
                var excluded = target
                var attributes = URLResourceValues(); attributes.isExcludedFromBackup = true
                try excluded.setResourceValues(attributes)
            }
            lock.lock(); completed = index + 1; lock.unlock()
        }
    }
}

extension NSError {
    static func study(_ message: String) -> NSError {
        NSError(domain: "IOWOStudy", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }
}
