// Apple system assets are installed locally through Speech; never bundled here.
import Speech
import Foundation
import Dispatch
let install = CommandLine.arguments.dropFirst().contains("--install")
let valid = CommandLine.arguments.count == 2 && ["--status", "--install"].contains(CommandLine.arguments[1])
guard valid else { fputs("Usage: install-speech-assets --status|--install\n", stderr); exit(2) }
Task {
    var result: [String: Any] = ["locale": "zh_CN", "install_requested": install, "audio_read": false]
    var code: Int32 = 0
    do {
        if #available(macOS 26.0, *) {
            let module = SpeechTranscriber(locale: Locale(identifier: "zh_CN"), preset: .transcription)
            result["status"] = String(describing: await AssetInventory.status(forModules: [module]))
            if install {
                _ = try await AssetInventory.reserve(locale: Locale(identifier: "zh_CN"))
                if let request = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
                    try await request.downloadAndInstall()
                }
                let state = await AssetInventory.status(forModules: [module])
                result["status"] = String(describing: state)
                if state != .installed { code = 1 }
            }
            // A fresh module may report supported before reservation despite a cached
            // language; report installedLocales separately instead of guessing absence.
            result["installed_locale"] = await SpeechTranscriber.installedLocales.contains { ["zh_CN", "zh-CN"].contains($0.identifier) }
        } else {
            result["error"] = "Requires macOS 26+ and a compatible Speech framework; text input remains available."
            code = 1
        }
    } catch {
        let error = error as NSError
        result["error_domain"] = error.domain
        result["error_code"] = error.code
        code = 1
    }
    print(String(data: try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), encoding: .utf8)!)
    exit(code)
}
dispatchMain()
