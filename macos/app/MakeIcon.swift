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
        NSColor(srgbRed: 0.976, green: 0.329, blue: 0.157, alpha: 1).setFill()
        NSBezierPath(roundedRect: NSRect(x: 2, y: 2, width: 60, height: 60), xRadius: 13, yRadius: 13).fill()
        func polygon(_ points: [(CGFloat, CGFloat)], _ color: NSColor) {
            let path = NSBezierPath()
            path.move(to: NSPoint(x: points[0].0, y: points[0].1))
            for p in points.dropFirst() { path.line(to: NSPoint(x: p.0, y: p.1)) }
            path.close()
            color.setFill()
            path.fill()
        }
        polygon([(7,49),(15,14),(27,28),(36,12),(47,27),(55,16),(56,49),(46,49),(46,34),(36,48),(25,35),(17,51)],
                NSColor(srgbRed: 0.094, green: 0.094, blue: 0.082, alpha: 1))
        polygon([(23,23),(34,15),(29,31),(40,28),(29,47),(31,34),(20,37)],
                NSColor(srgbRed: 0.863, green: 0.945, blue: 0.192, alpha: 1))
        NSGraphicsContext.restoreGraphicsState()
        let suffix = scale == 2 ? "@2x" : ""
        let url = directory.appendingPathComponent("icon_\(size)x\(size)\(suffix).png")
        try bitmap.representation(using: .png, properties: [:])!.write(to: url)
    }
}
