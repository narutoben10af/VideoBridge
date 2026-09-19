@main struct ReaderTests {
    static func main() throws {
        assert(TimelinePolicy.canSeek(0, in: [0..<10]))
        assert(!TimelinePolicy.canSeek(10, in: [0..<10]))
        assert(!TimelinePolicy.canSeek(12, in: [0..<10, 20..<30]))
        assert(!TimelinePolicy.canSeek(.nan, in: [0..<10]))
        assert(!TimelinePolicy.canSeek(-1, in: [0..<10]))
        assert(TimelinePolicy.clock(947) == "15:47")
        assert(TimelinePolicy.clock(3661) == "1:01:01")
        print("7 timeline boundary checks passed")
        let fm = FileManager.default
        let inbox = testHome.appendingPathComponent("Library/Application Support/VideoBridge/Inbox")
        try fm.createDirectory(at: inbox, withIntermediateDirectories: true)
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: inbox.path)
        defer { try? fm.removeItem(at: testHome) }
        let id = UUID().uuidString.lowercased()
        let url = URL(string: "videobridge://request/" + id)!
        let file = inbox.appendingPathComponent(id + ".json")
        let payload: [String: Any] = ["url":"https://example.com/movie.mp4", "title":"Test", "referer":"", "currentTime":0, "subtitles":[["url":"https://example.com/en.vtt", "label":"English", "language":"en"]]]
        var envelope: [String: Any] = ["protocolVersion":1,"requestId":id,"type":"offerMedia","createdAt":Date().timeIntervalSince1970,"payload":payload]
        func write(_ value: [String: Any]) throws {
            try JSONSerialization.data(withJSONObject: value).write(to:file)
            try fm.setAttributes([.posixPermissions:0o600],ofItemAtPath:file.path)
        }
        func rejected(_ label: String, _ input: URL = url) {
            do { _ = try BrowserInbox.consume(input); fatalError("Accepted " + label) }
            catch { print("PASS " + label) }
        }
        try write(envelope)
        let accepted = try BrowserInbox.consume(url); assert(accepted.title == "Test")
        print("PASS valid request")
        rejected("replay")
        envelope["createdAt"] = Date().timeIntervalSince1970 - 61
        try write(envelope); rejected("expired")
        envelope["createdAt"] = Date().timeIntervalSince1970
        try write(envelope)
        try fm.setAttributes([.posixPermissions:0o644],ofItemAtPath:file.path)
        rejected("insecure file permissions"); try fm.removeItem(at:file)
        try write(envelope)
        try fm.setAttributes([.posixPermissions:0o755],ofItemAtPath:inbox.path)
        rejected("insecure directory permissions")
        try fm.setAttributes([.posixPermissions:0o700],ofItemAtPath:inbox.path)
        try fm.removeItem(at:file)
        let target=inbox.appendingPathComponent("target.json")
        try JSONSerialization.data(withJSONObject:envelope).write(to:target)
        try fm.createSymbolicLink(at:file,withDestinationURL:target)
        rejected("symlink file"); try fm.removeItem(at:file)
        envelope["extra"] = "no"
        try write(envelope); rejected("unknown envelope field")
        envelope.removeValue(forKey:"extra")
        envelope["requestId"] = UUID().uuidString.lowercased()
        try write(envelope); rejected("request ID mismatch")
        rejected("legacy descriptor URL",URL(string:"videobridge://open?data=xxx")!)
        print("9 inbox reader checks passed")
    }
}
