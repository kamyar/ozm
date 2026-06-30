import SwiftUI

/// Lightweight, dependency-free syntax highlighter.
///
/// Works line-by-line (so multi-line constructs like triple-quoted strings or
/// block comments are not tracked across lines), which is good enough for the
/// short script/diff previews shown in approval dialogs. The language is chosen
/// from the pygments lexer name forwarded by the CLI in `payload.syntax`.
///
/// Colors mirror VS Code's built-in Default Dark Modern / Light Modern themes.
enum SyntaxHighlighter {
    /// Token colors for one appearance, matching VS Code's defaults.
    struct Palette {
        let comment: Color
        let string: Color
        let number: Color
        let control: Color   // keyword.control — if/for/return/import …
        let storage: Color   // storage.type / constant.language — def/class/True …

        /// VS Code "Default Dark Modern".
        static let dark = Palette(
            comment: Color(hex: 0x6A9955),
            string: Color(hex: 0xCE9178),
            number: Color(hex: 0xB5CEA8),
            control: Color(hex: 0xC586C0),
            storage: Color(hex: 0x569CD6)
        )

        /// VS Code "Default Light Modern".
        static let light = Palette(
            comment: Color(hex: 0x008000),
            string: Color(hex: 0xA31515),
            number: Color(hex: 0x098658),
            control: Color(hex: 0xAF00DB),
            storage: Color(hex: 0x0000FF)
        )

        static func forScheme(_ scheme: ColorScheme) -> Palette {
            scheme == .dark ? .dark : .light
        }
    }

    private struct Spec {
        let lineComments: [String]
        let keywords: Set<String>
    }

    static func highlight(_ line: String, syntax: String?, palette: Palette) -> AttributedString {
        let spec = spec(for: syntax)
        let chars = Array(line)
        let n = chars.count
        var out = AttributedString()
        var i = 0

        func emit(_ text: String, _ color: Color?) {
            var piece = AttributedString(text)
            if let color { piece.foregroundColor = color }
            out.append(piece)
        }

        while i < n {
            let c = chars[i]

            // Line comment — color the remainder of the line.
            if matchesComment(chars, at: i, markers: spec.lineComments) {
                emit(String(chars[i...]), palette.comment)
                break
            }

            // String literal (single, double, or backtick quoted).
            if c == "\"" || c == "'" || c == "`" {
                let start = i
                i += 1
                while i < n {
                    if chars[i] == "\\" && i + 1 < n {
                        i += 2
                        continue
                    }
                    if chars[i] == c {
                        i += 1
                        break
                    }
                    i += 1
                }
                emit(String(chars[start..<i]), palette.string)
                continue
            }

            // Number literal.
            if c.isNumber {
                let start = i
                while i < n, chars[i].isHexDigit || chars[i] == "." || chars[i] == "_"
                    || chars[i] == "x" || chars[i] == "X" {
                    i += 1
                }
                emit(String(chars[start..<i]), palette.number)
                continue
            }

            // Identifier / keyword.
            if c.isLetter || c == "_" {
                let start = i
                while i < n, chars[i].isLetter || chars[i].isNumber || chars[i] == "_" {
                    i += 1
                }
                let word = String(chars[start..<i])
                if spec.keywords.contains(word) {
                    emit(word, controlKeywords.contains(word) ? palette.control : palette.storage)
                } else {
                    emit(word, nil)
                }
                continue
            }

            emit(String(c), nil)
            i += 1
        }

        return out
    }

    private static func matchesComment(_ chars: [Character], at i: Int, markers: [String]) -> Bool {
        for marker in markers {
            let m = Array(marker)
            if i + m.count <= chars.count, Array(chars[i..<(i + m.count)]) == m {
                return true
            }
        }
        return false
    }

    private static func spec(for syntax: String?) -> Spec {
        let s = (syntax ?? "").lowercased()
        if s.contains("python") {
            return Spec(lineComments: ["#"], keywords: pythonKeywords)
        }
        if s.contains("bash") || s.contains("shell") || s.contains("sh") || s.contains("zsh") {
            return Spec(lineComments: ["#"], keywords: shellKeywords)
        }
        if s.contains("javascript") || s.contains("typescript") || s.contains("json")
            || s.contains("c++") || s.contains("c#") || s.contains("java")
            || s.contains("go") || s.contains("rust") || s.contains("swift") {
            return Spec(lineComments: ["//"], keywords: cFamilyKeywords)
        }
        // Unknown: accept both common comment styles and a broad keyword union.
        return Spec(lineComments: ["#", "//"], keywords: genericKeywords)
    }

    /// Flow-control keywords get VS Code's purple; everything else in a language's
    /// keyword set (declarations, types, language constants) gets the blue storage color.
    private static let controlKeywords: Set<String> = [
        "if", "elif", "else", "then", "fi", "for", "while", "until", "do", "done",
        "case", "esac", "switch", "break", "continue", "return", "yield", "await",
        "async", "try", "except", "catch", "finally", "throw", "throws", "raise",
        "with", "as", "in", "is", "and", "or", "not", "pass", "del", "assert",
        "import", "from", "export", "default", "match", "where", "guard", "defer",
        "select", "lambda", "global", "nonlocal",
    ]

    private static let pythonKeywords: Set<String> = [
        "and", "as", "assert", "async", "await", "break", "class", "continue",
        "def", "del", "elif", "else", "except", "finally", "for", "from",
        "global", "if", "import", "in", "is", "lambda", "match", "case",
        "nonlocal", "not", "or", "pass", "raise", "return", "try", "while",
        "with", "yield", "None", "True", "False", "self",
    ]

    private static let shellKeywords: Set<String> = [
        "if", "then", "else", "elif", "fi", "for", "while", "until", "do",
        "done", "case", "esac", "function", "in", "select", "return", "local",
        "export", "readonly", "declare", "set", "unset", "source", "echo",
        "cd", "exit", "trap", "shift",
    ]

    private static let cFamilyKeywords: Set<String> = [
        "const", "let", "var", "function", "func", "fn", "return", "if", "else",
        "for", "while", "do", "switch", "case", "break", "continue", "class",
        "struct", "enum", "interface", "extends", "implements", "new", "this",
        "self", "super", "import", "export", "from", "default", "try", "catch",
        "finally", "throw", "throws", "typeof", "instanceof", "void", "null",
        "nil", "undefined", "true", "false", "async", "await", "yield",
        "static", "public", "private", "protected", "package", "namespace",
        "template", "type", "typedef", "int", "float", "double", "char", "bool",
        "string", "map", "range", "defer", "go", "mut", "pub", "impl", "use",
        "match", "where", "guard",
    ]

    private static let genericKeywords: Set<String> =
        pythonKeywords.union(shellKeywords).union(cFamilyKeywords)
}

extension Color {
    /// Build a Color from a 0xRRGGBB integer.
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}
