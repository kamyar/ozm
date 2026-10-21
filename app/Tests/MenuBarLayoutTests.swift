import AppKit
import SwiftUI
import Testing
@testable import OzmApp

@Suite @MainActor
struct MenuBarLayoutTests {
    private func bluePixelCount(in bitmap: NSBitmapImageRep) throws -> Int {
        var count = 0
        for y in 0..<bitmap.pixelsHigh {
            for x in 0..<bitmap.pixelsWide {
                let color = try #require(bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
                if color.alphaComponent > 0.5,
                   color.blueComponent > color.redComponent + 0.25,
                   color.blueComponent > color.greenComponent + 0.2 {
                    count += 1
                }
            }
        }
        return count
    }

    @Test func switchKeepsItsOnColorWhenReopenedInAnInactiveWindow() throws {
        let queue = ApprovalQueue()
        let server = SocketServer()
        // Recreate the presentation without changing the shared mode.
        for handsFree in [true, true, false] {
            if queue.isHandsFree != handsFree {
                queue.isHandsFree = handsFree
            }
            let view = NSHostingView(rootView:
                MenuBarView(queue: queue, server: server)
                    .environment(\.controlActiveState, .inactive)
                    .environment(\.colorScheme, .dark)
                    .background(Color(nsColor: .windowBackgroundColor))
            )
            view.appearance = NSAppearance(named: .darkAqua)
            view.frame = NSRect(origin: .zero, size: view.fittingSize)
            let window = NSWindow(
                contentRect: view.frame,
                styleMask: [.titled],
                backing: .buffered,
                defer: false
            )
            window.isReleasedWhenClosed = false
            window.contentView = view
            defer { window.close() }
            #expect(!window.isKeyWindow)
            view.layoutSubtreeIfNeeded()
            let bitmap = try #require(view.bitmapImageRepForCachingDisplay(in: view.bounds))
            view.cacheDisplay(in: view.bounds, to: bitmap)
            let bluePixels = try bluePixelCount(in: bitmap)
            #expect((bluePixels > 0) == handsFree)
            #expect(queue.isHandsFree == handsFree)
        }
    }

    @Test func handsFreeIndicatorAddsOnlyADot() {
        let off = MenuBarIcon.image(handsFree: false).size
        let on = MenuBarIcon.image(handsFree: true).size
        #expect(on.width > off.width)
        #expect(on.width - off.width <= 8)
        #expect(on.height == off.height)
    }

    @Test func statusIconContainsBluePixelsOnlyWhenHandsFreeIsOn() throws {
        for handsFree in [false, true] {
            let image = MenuBarIcon.image(handsFree: handsFree)
            #expect(image.isTemplate == !handsFree)
            let tiff = try #require(image.tiffRepresentation)
            let bitmap = try #require(NSBitmapImageRep(data: tiff))
            #expect(try (bluePixelCount(in: bitmap) > 0) == handsFree)
        }
    }

    @Test func popupDoesNotGrowToFillLargeWindowProposals() {
        for handsFree in [false, true] {
            let queue = ApprovalQueue()
            queue.isHandsFree = handsFree
            let server = SocketServer()
            server.isRunning = true
            let controller = NSHostingController(rootView: MenuBarView(queue: queue, server: server))
            let expected = controller.sizeThatFits(in: NSSize(width: 420, height: 200))
            #expect(expected.width == 420)
            #expect(expected.height < 200)
            for proposedHeight in [CGFloat(1), 500, 1000] {
                let size = controller.sizeThatFits(in: NSSize(width: 420, height: proposedHeight))
                #expect(size == expected)
            }
        }
    }

    @Test func nativePopoverResizesWhenTheModeChanges() async {
        let queue = ApprovalQueue()
        let server = SocketServer()
        let popover = MenuBarPopover(queue: queue, server: server)
        let offSize = popover.contentSize
        #expect(offSize.width == 420)
        #expect(offSize.height < 200)

        queue.isHandsFree = true
        await Task.yield()
        popover.contentViewController?.view.setFrameSize(NSSize(width: 420, height: 500))
        popover.resizeToFit()
        #expect(popover.contentSize.height > offSize.height)
        #expect(popover.contentSize.height < 200)
        #expect(popover.contentViewController?.view.frame.size == popover.contentSize)

        queue.isHandsFree = false
        await Task.yield()
        popover.resizeToFit()
        #expect(popover.contentSize == offSize)
    }

    @Test func approvalModesKeepTheMenuCompact() throws {
        var heights: [CGFloat] = []
        for handsFree in [false, true] {
            let queue = ApprovalQueue()
            queue.isHandsFree = handsFree
            let server = SocketServer()
            // Show the listening state without starting a socket server.
            server.isRunning = true
            let view = NSHostingView(rootView:
                MenuBarView(queue: queue, server: server)
                    .environment(\.colorScheme, .dark)
                    .background(Color(nsColor: .windowBackgroundColor))
            )
            view.appearance = NSAppearance(named: .darkAqua)
            let size = view.fittingSize
            #expect(size.width == 420)
            #expect(size.height < 200)
            heights.append(size.height)
            view.frame = NSRect(origin: .zero, size: size)
            view.layoutSubtreeIfNeeded()
            let bitmap = try #require(view.bitmapImageRepForCachingDisplay(in: view.bounds))
            view.cacheDisplay(in: view.bounds, to: bitmap)
            let png = try #require(bitmap.representation(using: .png, properties: [:]))
            let directory = URL(fileURLWithPath: #filePath)
                .deletingLastPathComponent().deletingLastPathComponent()
                .appendingPathComponent(".build/previews", isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try png.write(to: directory.appendingPathComponent(handsFree ? "hands-free-on.png" : "hands-free-off.png"))
        }
        #expect(heights[1] > heights[0])
        #expect(heights[1] - heights[0] <= 32)
    }
}
