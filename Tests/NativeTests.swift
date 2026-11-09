import XCTest
import PDFKit
@testable import IOWOBibleStudy

final class NativeTests: XCTestCase {
    @MainActor func testUnicodeRichSermonPDF() throws {
        let note = ##"{"content":{"blocks":[{"type":"heading","runs":[{"text":"Faith and pagtuo","bold":true,"font_size":18}]},{"type":"scripture","runs":[{"text":"Love and hope.\n","italic":true,"underline":true,"highlight":"#fffeb3","color":"#123456"}]}]}}"##
        let data = try SermonPDF.create(note)
        let document = try XCTUnwrap(PDFDocument(data: data))
        XCTAssertGreaterThan(document.pageCount, 0)
        XCTAssertTrue(document.string?.contains("Faith and pagtuo") == true)
        XCTAssertTrue(document.string?.contains("Love and hope") == true)
    }

    @MainActor func testLongSermonUsesMultiplePages() throws {
        let blocks: [[String: Any]] = (0..<180).map { index in
            ["type": "paragraph", "runs": [["text": "Sermon paragraph \(index): Study the Word and put it into practice."]]]
        }
        let json = try JSONSerialization.data(withJSONObject: ["content": ["blocks": blocks]])
        let data = try SermonPDF.create(String(decoding: json, as: UTF8.self))
        XCTAssertGreaterThan(try XCTUnwrap(PDFDocument(data: data)).pageCount, 1)
    }

    @MainActor func testInvalidSermonCannotExport() {
        XCTAssertThrowsError(try SermonPDF.create("{}"))
    }
}
