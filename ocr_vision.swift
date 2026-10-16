import AppKit
import Foundation
import PDFKit
import Vision

guard CommandLine.arguments.count >= 2 else { exit(2) }
let thumbnail = CommandLine.arguments[1] == "--thumbnail"
let fileIndex = thumbnail ? 2 : 1
guard CommandLine.arguments.count > fileIndex else { exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[fileIndex])
let image: NSImage
if url.pathExtension.lowercased() == "pdf" {
    guard CommandLine.arguments.count > fileIndex + 1,
          let number = Int(CommandLine.arguments[fileIndex + 1]),
          let document = PDFDocument(url: url),
          number > 0 && number <= document.pageCount,
          let page = document.page(at: number - 1) else { exit(2) }
    image = page.thumbnail(of: thumbnail ? CGSize(width: 480, height: 680) : CGSize(width: 1600, height: 2200), for: .mediaBox)
} else {
    guard let source = NSImage(contentsOf: url) else { exit(2) }
    image = source
}
if thumbnail {
    let scale = min(1.0, 480.0 / max(image.size.width, 1.0), 680.0 / max(image.size.height, 1.0))
    let size = CGSize(width: max(1, image.size.width * scale), height: max(1, image.size.height * scale))
    let small = NSImage(size: size)
    small.lockFocus()
    image.draw(in: CGRect(origin: .zero, size: size))
    small.unlockFocus()
    guard let tiff = small.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let data = bitmap.representation(using: .png, properties: [:]) else { exit(2) }
    print(data.base64EncodedString())
    exit(0)
}
guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(2) }
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
let available = (try? VNRecognizeTextRequest.supportedRecognitionLanguages(for: .accurate, revision: request.revision)) ?? []
let preferred = ["en-US", "zh-Hans"].filter { available.contains($0) }
if !preferred.isEmpty { request.recognitionLanguages = preferred }
do {
    try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
    for observation in request.results ?? [] {
        if let text = observation.topCandidates(1).first?.string { print(text) }
    }
} catch {
    fputs("Vision OCR failed: \(error)\n", stderr)
    exit(1)
}
