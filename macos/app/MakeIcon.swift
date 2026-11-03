import AppKit
import Foundation

guard CommandLine.arguments.count == 2 else { fatalError("Usage: MakeIcon.swift <output.iconset>") }
let directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        let context = NSGraphicsContext(bitmapImageRep: bitmap)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = context
        let transform = NSAffineTransform()
        transform.translateX(by: 0, yBy: CGFloat(pixels))
        transform.scaleX(by: CGFloat(pixels) / 64, yBy: -CGFloat(pixels) / 64)
        transform.concat()
        // Exact colors and paths from src/assets/madsense.svg. The macOS icon
        // must not redraw the M or alter the established mark's proportions.
        NSColor(srgbRed: 249 / 255, green: 84 / 255, blue: 40 / 255, alpha: 1).setFill()
        NSBezierPath(rect: NSRect(x: 0, y: 0, width: 64, height: 64)).fill()
        func polygon(_ points: [(CGFloat, CGFloat)], _ color: NSColor) {
            let path = NSBezierPath()
            path.move(to: NSPoint(x: points[0].0, y: points[0].1))
            for p in points.dropFirst() { path.line(to: NSPoint(x: p.0, y: p.1)) }
            path.close()
            color.setFill()
            path.fill()
        }
        polygon([(3,51),(13,10),(27,26),(36,8),(47,24),(58,11),(59,52),(46,52),(46,34),(36,48),(25,35),(17,54)],
                NSColor(srgbRed: 24 / 255, green: 24 / 255, blue: 21 / 255, alpha: 1))
        polygon([(23,23),(34,15),(29,31),(40,28),(29,47),(31,34),(20,37)],
                NSColor(srgbRed: 220 / 255, green: 241 / 255, blue: 49 / 255, alpha: 1))
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        let url = directory.appendingPathComponent("icon_\(size)x\(size)\(suffix).png")
        try bitmap.representation(using: .png, properties: [:])!.write(to: url)
    }
}
