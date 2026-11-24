import AppKit
import XCTest
@testable import Search

/// Clipboard fallback for embedded extension frames (Vimium's yy, for one):
/// writes only right after a real click or key in a page the extension may
/// reach, consumes that gesture on success, and marks private-tab copies
/// transient so clipboard managers skip them.
@available(macOS 15.4, *)
@MainActor
final class ExtensionClipboardTests: XCTestCase {
    private let pageURL = URL(string: "https://example.com/article")!
    private let transientType = NSPasteboard.PasteboardType("org.nspasteboard.TransientType")

    private func makeBoard() -> NSPasteboard {
        let board = NSPasteboard(name: .init("search.ext-clipboard.test.\(UUID().uuidString)"))
        board.clearContents()
        addTeardownBlock { board.releaseGlobally() }
        return board
    }

    func testRecentGestureWritesOnceAndConsumesGesture() throws {
        let board = makeBoard()
        let now = Date()
        var touch: Extensions.PageTouch? = .init(url: pageURL, shy: false, at: now.addingTimeInterval(-0.2))

        try ExtensionShims.writeClipboard(
            pageURL.absoluteString,
            touch: &touch,
            now: now,
            appActive: true,
            hasAccess: { $0 == pageURL },
            hasPrivateAccess: false,
            to: board
        )

        XCTAssertEqual(board.string(forType: .string), pageURL.absoluteString)
        XCTAssertNil(board.data(forType: transientType))
        XCTAssertNil(touch)

        XCTAssertThrowsError(try ExtensionShims.writeClipboard(
            "second",
            touch: &touch,
            now: now,
            appActive: true,
            hasAccess: { _ in true },
            hasPrivateAccess: false,
            to: board
        ))
        XCTAssertEqual(board.string(forType: .string), pageURL.absoluteString)
    }

    func testRefusesMissingStaleAndInactiveAppGestures() {
        let board = makeBoard()
        board.setString("kept", forType: .string)
        let now = Date()

        var none: Extensions.PageTouch? = nil
        XCTAssertThrowsError(try ExtensionShims.writeClipboard(
            "bad", touch: &none, now: now, appActive: true,
            hasAccess: { _ in true }, hasPrivateAccess: false, to: board
        ))

        var stale: Extensions.PageTouch? = .init(url: pageURL, shy: false, at: now.addingTimeInterval(-2.1))
        XCTAssertThrowsError(try ExtensionShims.writeClipboard(
            "bad", touch: &stale, now: now, appActive: true,
            hasAccess: { _ in true }, hasPrivateAccess: false, to: board
        ))

        var inactive: Extensions.PageTouch? = .init(url: pageURL, shy: false, at: now.addingTimeInterval(-0.1))
        XCTAssertThrowsError(try ExtensionShims.writeClipboard(
            "bad", touch: &inactive, now: now, appActive: false,
            hasAccess: { _ in true }, hasPrivateAccess: false, to: board
        ))

        XCTAssertEqual(board.string(forType: .string), "kept")
    }

    func testRefusesUnauthorizedSiteWithoutConsumingGesture() {
        let board = makeBoard()
        board.setString("kept", forType: .string)
        let now = Date()
        var touch: Extensions.PageTouch? = .init(url: pageURL, shy: false, at: now.addingTimeInterval(-0.1))

        XCTAssertThrowsError(try ExtensionShims.writeClipboard(
            "stolen", touch: &touch, now: now, appActive: true,
            hasAccess: { _ in false }, hasPrivateAccess: false, to: board
        ))
        XCTAssertNotNil(touch)
        XCTAssertEqual(board.string(forType: .string), "kept")
    }

    func testPrivateTabRequiresPrivateAccessAndMarksTransient() throws {
        let board = makeBoard()
        board.setString("kept", forType: .string)
        let now = Date()
        var touch: Extensions.PageTouch? = .init(url: pageURL, shy: true, at: now.addingTimeInterval(-0.1))

        XCTAssertThrowsError(try ExtensionShims.writeClipboard(
            "secret", touch: &touch, now: now, appActive: true,
            hasAccess: { _ in true }, hasPrivateAccess: false, to: board
        ))
        XCTAssertEqual(board.string(forType: .string), "kept")

        try ExtensionShims.writeClipboard(
            "secret", touch: &touch, now: now, appActive: true,
            hasAccess: { _ in true }, hasPrivateAccess: true, to: board
        )
        XCTAssertEqual(board.string(forType: .string), "secret")
        XCTAssertNotNil(board.data(forType: transientType))
        XCTAssertNil(touch)
    }

    func testRefusesOversizedText() {
        let board = makeBoard()
        board.setString("kept", forType: .string)
        let now = Date()
        var touch: Extensions.PageTouch? = .init(url: pageURL, shy: false, at: now.addingTimeInterval(-0.1))
        let huge = String(repeating: "a", count: 10 * 1024 * 1024 + 1)

        XCTAssertThrowsError(try ExtensionShims.writeClipboard(
            huge, touch: &touch, now: now, appActive: true,
            hasAccess: { _ in true }, hasPrivateAccess: false, to: board
        ))
        XCTAssertEqual(board.string(forType: .string), "kept")
    }
}
