import UIKit
import CoreText

enum SermonPDF {
    private static func color(_ value: Any?) -> UIColor? {
        guard let text = value as? String, text.hasPrefix("#"), text.count == 7,
              let number = UInt32(text.dropFirst(), radix: 16) else { return nil }
        return UIColor(red: CGFloat((number >> 16) & 255) / 255,
                       green: CGFloat((number >> 8) & 255) / 255, blue: CGFloat(number & 255) / 255, alpha: 1)
    }

    static func create(_ json: String) throws -> Data {
        guard let note = try JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any],
              let content = note["content"] as? [String: Any], let blocks = content["blocks"] as? [[String: Any]] else {
            throw NSError.study("The sermon could not be exported.")
        }
        let page = CGRect(x: 0, y: 0, width: 595, height: 842)
        let sheet = page.insetBy(dx: 36, dy: 36)
        let renderer = UIGraphicsPDFRenderer(bounds: page)
        var renderingError: Error?
        let data = renderer.pdfData { context in
            var y = sheet.minY
            var number = 0
            func nextPage() {
                context.beginPage(); number += 1; y = sheet.minY
                UIColor.white.setFill(); context.cgContext.fill(page)
                ("\(number)" as NSString).draw(at: CGPoint(x: page.width - 50, y: page.height - 24),
                    withAttributes: [.font: UIFont.systemFont(ofSize: 8), .foregroundColor: UIColor.darkGray])
            }
            nextPage()
            for block in blocks {
                let kind = block["type"] as? String ?? "paragraph"
                if block["page_break"] as? Bool == true && y > sheet.minY { nextPage() }
                let paragraph = NSMutableParagraphStyle()
                switch block["align"] as? String {
                case "center": paragraph.alignment = .center
                case "right": paragraph.alignment = .right
                case "justify": paragraph.alignment = .justified
                default: paragraph.alignment = .left
                }
                paragraph.lineSpacing = 2
                let text = NSMutableAttributedString(string: "")
                for run in block["runs"] as? [[String: Any]] ?? [] {
                    let size = CGFloat(max(6, min(72, run["font_size"] as? Double ?? 11)))
                    let bold = run["bold"] as? Bool ?? ["main_point", "subpoint", "heading", "conclusion"].contains(kind)
                    let italic = run["italic"] as? Bool ?? (kind == "scripture")
                    let family = run["font_family"] as? String ?? "Noto Sans"
                    let base: UIFont
                    if family == "Courier New" { base = UIFont.monospacedSystemFont(ofSize: size, weight: bold ? .bold : .regular) }
                    else if ["Times New Roman", "Cambria", "Georgia"].contains(family) { base = UIFont(name: "Georgia", size: size) ?? UIFont.systemFont(ofSize: size) }
                    else { base = UIFont(name: "NotoSans-Regular", size: size) ?? UIFont.systemFont(ofSize: size) }
                    var traits: UIFontDescriptor.SymbolicTraits = []
                    if bold { traits.insert(.traitBold) }; if italic { traits.insert(.traitItalic) }
                    let descriptor = base.fontDescriptor.withSymbolicTraits(traits) ?? base.fontDescriptor
                    var attributes: [NSAttributedString.Key: Any] = [.font: UIFont(descriptor: descriptor, size: size),
                        .paragraphStyle: paragraph, .foregroundColor: color(run["color"]) ?? UIColor.black]
                    attributes[NSAttributedString.Key(kCTForegroundColorAttributeName as String)] = (color(run["color"]) ?? UIColor.black).cgColor
                    if run["underline"] as? Bool == true {
                        attributes[.underlineStyle] = NSUnderlineStyle.single.rawValue
                        attributes[NSAttributedString.Key(kCTUnderlineStyleAttributeName as String)] = CTUnderlineStyle.single.rawValue
                    }
                    if let highlight = color(run["highlight"]) { attributes[.backgroundColor] = highlight }
                    if run["superscript"] as? Bool == true { attributes[.baselineOffset] = size * 0.35; attributes[.font] = UIFont(descriptor: descriptor, size: size * 0.75) }
                    text.append(NSAttributedString(string: run["text"] as? String ?? "", attributes: attributes))
                }
                if text.length == 0 { text.append(NSAttributedString(string: " ", attributes: [.font: UIFont.systemFont(ofSize: 11)])) }
                let defaultIndent = ["subpoint", "illustration"].contains(kind) ? 18.0 : kind == "sub_subpoint" ? 36.0 : 0
                let indent = CGFloat(max(0, min(160, block["indent"] as? Double ?? defaultIndent)))
                if ["main_point", "subpoint", "heading", "theme", "text"].contains(kind) && sheet.maxY - y < 72 { nextPage() }
                let framesetter = CTFramesetterCreateWithAttributedString(text)
                var offset = 0
                while offset < text.length {
                    let height = sheet.maxY - y
                    if height < 20 { nextPage(); continue }
                    let path = CGPath(rect: CGRect(x: sheet.minX + indent, y: sheet.minY,
                                                  width: sheet.width - indent, height: height), transform: nil)
                    let frame = CTFramesetterCreateFrame(framesetter, CFRange(location: offset, length: 0), path, nil)
                    let visible = CTFrameGetVisibleStringRange(frame)
                    guard visible.length > 0 else {
                        // Validation bounds fonts to 72pt, which fits a fresh sheet.
                        if y == sheet.minY { renderingError = NSError.study("A sermon block could not fit on the exported page."); return }
                        nextPage(); continue
                    }
                    let cg = context.cgContext
                    cg.saveGState(); cg.translateBy(x: 0, y: page.height); cg.scaleBy(x: 1, y: -1)
                    drawHighlights(frame, context: cg, bounds: path.boundingBoxOfPath)
                    CTFrameDraw(frame, cg); cg.restoreGState()
                    offset += visible.length
                    if offset < text.length { nextPage() }
                    else {
                        let suggested = CTFramesetterSuggestFrameSizeWithConstraints(framesetter,
                            CFRange(location: visible.location, length: visible.length), nil,
                            CGSize(width: sheet.width - indent, height: .greatestFiniteMagnitude), nil)
                        y += ceil(suggested.height) + CGFloat(max(0, min(36, block["space_after"] as? Double ?? 8)))
                    }
                }
            }
        }
        if let renderingError { throw renderingError }
        return data
    }

    private static func drawHighlights(_ frame: CTFrame, context: CGContext, bounds: CGRect) {
        let lines = CTFrameGetLines(frame) as! [CTLine]
        var origins = [CGPoint](repeating: .zero, count: lines.count)
        CTFrameGetLineOrigins(frame, CFRange(location: 0, length: 0), &origins)
        for (index, line) in lines.enumerated() {
            for run in CTLineGetGlyphRuns(line) as! [CTRun] {
                let attributes = CTRunGetAttributes(run) as NSDictionary
                guard let color = attributes[NSAttributedString.Key.backgroundColor.rawValue] as? UIColor else { continue }
                let range = CTRunGetStringRange(run)
                var ascent: CGFloat = 0, descent: CGFloat = 0
                _ = CTRunGetTypographicBounds(run, CFRange(location: 0, length: 0), &ascent, &descent, nil)
                let first = CTLineGetOffsetForStringIndex(line, range.location, nil)
                let last = CTLineGetOffsetForStringIndex(line, range.location + range.length, nil)
                context.setFillColor(color.cgColor)
                context.fill(CGRect(x: bounds.minX + origins[index].x + min(first, last),
                    y: bounds.minY + origins[index].y - descent, width: max(1, abs(last - first)), height: ascent + descent))
            }
        }
    }
}
