import AppKit

/// Keep the shield and blue indicator in one native status-item image.
enum MenuBarIcon {
    static func image(handsFree: Bool) -> NSImage {
        let size = NSSize(width: handsFree ? 25 : 18, height: 18)
        let image = NSImage(size: size, flipped: false) { _ in
            let symbol = NSImage(
                systemSymbolName: "shield.checkered",
                accessibilityDescription: "Ozm"
            )?.withSymbolConfiguration(.init(paletteColors: [.labelColor]))
            symbol?.draw(in: NSRect(x: 1, y: 1, width: 16, height: 16))
            if handsFree {
                NSColor.systemBlue.setFill()
                NSBezierPath(ovalIn: NSRect(x: 20, y: 7, width: 4, height: 4)).fill()
            }
            return true
        }
        // Template rendering would turn the blue dot monochrome.
        image.isTemplate = !handsFree
        return image
    }
}
