import XCTest
@testable import WeeOrchestratorClient

/// Regression test for issue #509: the composer TextField clipped overflow
/// text instead of scrolling to it. Wrapping it in a ScrollView fixes the
/// clipping, but a ScrollView with no size of its own asks to fill whatever
/// height its parent offers -- these tests cover the clamping math that
/// keeps the input bar hugging short content instead of always sitting near
/// its 200pt cap.
final class ChatViewInputBarHeightTests: XCTestCase {
    func test_shortSingleLineTextStaysAtTheOneLineFloor() {
        // A fresh/empty TextField reports a near-zero or small measured
        // height before layout settles; it must never render smaller than
        // one line.
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 0), 36)
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 20), 36)
    }

    func test_heightWithinTheNormalRangePassesThroughUnchanged() {
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 80), 80)
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 199), 199)
    }

    func test_overflowingTextIsClampedAtTheCapNotAllowedToGrowUnbounded() {
        // This is the regression case: before fixedSize was replaced with
        // measured-height clamping, a ScrollView with no content size of
        // its own would ask to fill all available space rather than
        // respecting this cap.
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 200), 200)
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 500), 200)
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 10_000), 200)
    }

    func test_boundariesAreInclusive() {
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 36), 36)
        XCTAssertEqual(ChatView.inputBarHeight(forMeasuredTextHeight: 200), 200)
    }
}
