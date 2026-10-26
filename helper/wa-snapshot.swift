// Copie en lecture seule de la base WhatsApp Desktop vers ~/Library/Caches/whatsapp-mcp/snapshot.sqlite.
// Seul ce programme reçoit l'accès complet au disque. Il est lancé par launchd, jamais par le terminal,
// pour que macOS lui attribue l'accès à lui et non à l'app qui l'aurait démarré.
import Foundation
import SQLite3

let home = FileManager.default.homeDirectoryForCurrentUser
let source = home.appendingPathComponent(
    "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite")
let destDir = home.appendingPathComponent("Library/Caches/whatsapp-mcp")
let dest = destDir.appendingPathComponent("snapshot.sqlite")
let tmp = destDir.appendingPathComponent("snapshot.tmp")

func log(_ msg: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    FileHandle.standardError.write("\(stamp) \(msg)\n".data(using: .utf8)!)
}

func fail(_ msg: String) -> Never {
    log("ERREUR \(msg)")
    exit(1)
}

do {
    try FileManager.default.createDirectory(
        at: destDir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
} catch {
    fail("création de \(destDir.path) : \(error)")
}
try? FileManager.default.removeItem(at: tmp)

var src: OpaquePointer?
guard sqlite3_open_v2(source.path, &src, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
    fail("ouverture de la base : \(String(cString: sqlite3_errmsg(src)))")
}
defer { sqlite3_close(src) }

var dst: OpaquePointer?
guard sqlite3_open_v2(tmp.path, &dst, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) == SQLITE_OK else {
    fail("création de la copie : \(String(cString: sqlite3_errmsg(dst)))")
}

guard let backup = sqlite3_backup_init(dst, "main", src, "main") else {
    fail("préparation de la copie : \(String(cString: sqlite3_errmsg(dst)))")
}
var rc: Int32
var retries = 0
repeat {
    rc = sqlite3_backup_step(backup, -1)
    if rc == SQLITE_BUSY || rc == SQLITE_LOCKED {
        retries += 1
        usleep(200_000)
    }
} while (rc == SQLITE_BUSY || rc == SQLITE_LOCKED) && retries < 50
sqlite3_backup_finish(backup)
sqlite3_close(dst)

guard rc == SQLITE_DONE else {
    try? FileManager.default.removeItem(at: tmp)
    fail("copie interrompue (code \(rc)) : \(String(cString: sqlite3_errstr(rc)))")
}

chmod(tmp.path, 0o600)
guard rename(tmp.path, dest.path) == 0 else {
    fail("remplacement de \(dest.path) : \(String(cString: strerror(errno)))")
}
log("copie à jour")

// Copie à la demande de vocaux : seuls les chemins listés dans media-request.txt, relatifs au
// dossier Message, sous Media/ et en .opus, sont copiés vers ~/Library/Caches/whatsapp-mcp/media.
let request = destDir.appendingPathComponent("media-request.txt")
if let list = try? String(contentsOf: request, encoding: .utf8) {
    let messageDir = source.deletingLastPathComponent().appendingPathComponent("Message")
    let mediaDir = destDir.appendingPathComponent("media")
    var copied = 0, skipped = 0
    for raw in list.split(whereSeparator: \.isNewline) {
        let rel = raw.trimmingCharacters(in: .whitespaces)
        guard rel.hasPrefix("Media/"), rel.hasSuffix(".opus"), !rel.contains("..") else { skipped += 1; continue }
        let from = messageDir.appendingPathComponent(rel)
        let to = mediaDir.appendingPathComponent(rel)
        if FileManager.default.fileExists(atPath: to.path) { continue }
        do {
            try FileManager.default.createDirectory(
                at: to.deletingLastPathComponent(), withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            try FileManager.default.copyItem(at: from, to: to)
            chmod(to.path, 0o600)
            copied += 1
        } catch {
            skipped += 1
        }
    }
    try? FileManager.default.removeItem(at: request)
    log("vocaux copiés : \(copied), ignorés : \(skipped)")
}
