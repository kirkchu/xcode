import Foundation

@main
struct IncrementalTextDecoderRegression {
    static func main() {
        // Qwen fragments: 長 / 頸 (two tokens) / 鹿 / continuation.
        let fragments: [[UInt8]] = [Array("長".utf8), [0xE9, 0xA0], [0xB8], Array("鹿".utf8), Array("的英文是 giraffe。".utf8)]
        var decoder = IncrementalTextDecoder()
        var result = ""
        for index in fragments.indices {
            result += decoder.append(index) { ids in
                String(decoding: ids.flatMap { fragments[$0] }, as: UTF8.self)
            } ?? ""
        }
        precondition(result == "長頸鹿的英文是 giraffe。", result)
        for expected in ["長頸鹿的英文是 giraffe。", "Hello world!", "你好🙂，完成。", "e\u{301} 👍🏽 👩‍💻 done", "<think>中文思考</think>答案"] {
            var decoder = IncrementalTextDecoder()
            var output = ""
            for byte in expected.utf8 {
                output += decoder.append(Int(byte)) { ids in
                    String(decoding: ids.map { UInt8($0) }, as: UTF8.self)
                } ?? ""
            }
            precondition(output == expected, "Expected \(expected), got \(output)")
        }
        var partial = IncrementalTextDecoder()
        precondition(partial.append(65) { String(decoding: $0.map { UInt8($0) }, as: UTF8.self) } == "A")
        precondition(partial.append(0xE9) { String(decoding: $0.map { UInt8($0) }, as: UTF8.self) } == nil)
        print("PASS: Qwen split tokens, Chinese, ASCII, emoji, combining marks, reasoning markers, incomplete UTF-8")
    }
}
