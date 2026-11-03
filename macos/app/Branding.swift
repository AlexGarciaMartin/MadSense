import AppKit

// Native vector counterparts of src/MainWindow.cs. Colors and artwork geometry
// intentionally match Windows; backend state and translated copy come from the
// caller. No artwork view invents a connected or supported capability state.
enum BrandPalette {
    static let ink = color(24, 24, 21)       // #181815
    static let paper = color(240, 235, 220) // #F0EBDC
    static let acid = color(220, 241, 49)   // #DCF131
    static let hot = color(249, 84, 40)     // #F95428
    static let muted = color(99, 96, 85)    // #636055
    static let orange = hot
    static let lime = acid
    static let disabled = color(212, 209, 198)

    private static func color(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat) -> NSColor {
        NSColor(srgbRed: red / 255, green: green / 255, blue: blue / 255, alpha: 1)
    }

    static func displayFont(_ size: CGFloat) -> NSFont {
        // Impact is also available on some Macs. The fallback retains the heavy,
        // condensed printed headline instead of switching to a regular UI font.
        for name in ["Impact", "HelveticaNeue-CondensedBlack", "HelveticaNeue-CondensedBold", "ArialNarrow-Bold"] {
            if let font = NSFont(name: name, size: size) { return font }
        }
        let descriptor = NSFont.systemFont(ofSize: size, weight: .heavy).fontDescriptor
            .withSymbolicTraits([.bold, .condensed])
        return NSFont(descriptor: descriptor, size: size) ?? NSFont.systemFont(ofSize: size, weight: .heavy)
    }

    static func monoFont(_ size: CGFloat) -> NSFont {
        NSFont(name: "Menlo-Bold", size: size) ?? NSFont.monospacedSystemFont(ofSize: size, weight: .bold)
    }
}

private enum BrandDrawing {
    static func polygon(_ context: CGContext, _ points: [(CGFloat, CGFloat)], _ fill: NSColor) {
        guard let first = points.first else { return }
        context.beginPath()
        context.move(to: CGPoint(x: first.0, y: first.1))
        for point in points.dropFirst() { context.addLine(to: CGPoint(x: point.0, y: point.1)) }
        context.closePath()
        context.setFillColor(fill.cgColor)
        context.fillPath()
    }

    static func line(_ context: CGContext, _ start: CGPoint, _ end: CGPoint,
                     color: NSColor = BrandPalette.ink, width: CGFloat) {
        context.beginPath()
        context.move(to: start)
        context.addLine(to: end)
        context.setStrokeColor(color.cgColor)
        context.setLineWidth(width)
        context.strokePath()
    }

    static func logo(_ context: CGContext, in rect: CGRect) {
        context.saveGState()
        context.translateBy(x: rect.minX, y: rect.minY)
        context.scaleBy(x: rect.width / 64, y: rect.height / 64)
        polygon(context, [(3,51),(13,10),(27,26),(36,8),(47,24),(58,11),
                          (59,52),(46,52),(46,34),(36,48),(25,35),(17,54)], BrandPalette.ink)
        polygon(context, [(23,23),(34,15),(29,31),(40,28),(29,47),(31,34),(20,37)], BrandPalette.acid)
        context.restoreGState()
    }

    // All text is clipped to its allocated area. Normal translations fit by
    // reducing font size; unusually long copy truncates visually at the lower
    // bound while the accessibility label retains the full original string.
    static func text(_ text: String, in rect: CGRect, color: NSColor,
                     largest: CGFloat, smallest: CGFloat, display: Bool = false,
                     wrap: Bool = false, centered: Bool = false) {
        guard !text.isEmpty, rect.width > 0, rect.height > 0 else { return }
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = centered ? .center : .left
        paragraph.lineBreakMode = wrap ? .byWordWrapping : .byTruncatingTail
        var size = largest
        var attributed = NSAttributedString(string: "")
        var measured = CGRect.zero
        while true {
            let font = display ? BrandPalette.displayFont(size) : BrandPalette.monoFont(size)
            attributed = NSAttributedString(string: text, attributes: [
                .font: font, .foregroundColor: color, .paragraphStyle: paragraph
            ])
            measured = attributed.boundingRect(
                with: CGSize(width: wrap ? rect.width : 10000, height: 10000),
                options: [.usesLineFragmentOrigin, .usesFontLeading])
            if (measured.width <= rect.width + 0.5 && measured.height <= rect.height + 0.5) || size <= smallest { break }
            size = max(smallest, size - 0.5)
        }
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath(rect: rect).addClip()
        var destination = rect
        if centered { destination.origin.y += max(0, (rect.height - measured.height) / 2) }
        attributed.draw(with: destination, options: [.usesLineFragmentOrigin, .usesFontLeading])
        NSGraphicsContext.restoreGraphicsState()
    }

    static func controller(_ context: CGContext, in rect: CGRect, connected: Bool) {
        context.saveGState()
        defer { context.restoreGState() }
        context.translateBy(x: rect.minX, y: rect.minY)
        context.scaleBy(x: rect.width / 216, y: rect.height / 130)
        let body = CGMutablePath()
        body.move(to: CGPoint(x: 36, y: 16))
        body.addCurve(to: CGPoint(x: 14, y: 117), control1: CGPoint(x: 12, y: 23), control2: CGPoint(x: 0, y: 104))
        body.addCurve(to: CGPoint(x: 62, y: 103), control1: CGPoint(x: 30, y: 133), control2: CGPoint(x: 50, y: 114))
        body.addLine(to: CGPoint(x: 154, y: 103))
        body.addCurve(to: CGPoint(x: 202, y: 117), control1: CGPoint(x: 166, y: 114), control2: CGPoint(x: 186, y: 133))
        body.addCurve(to: CGPoint(x: 181, y: 16), control1: CGPoint(x: 218, y: 103), control2: CGPoint(x: 205, y: 23))
        body.addCurve(to: CGPoint(x: 36, y: 16), control1: CGPoint(x: 150, y: 6), control2: CGPoint(x: 65, y: 6))
        body.closeSubpath()
        context.saveGState()
        context.translateBy(x: 5, y: 7)
        context.setFillColor(BrandPalette.ink.cgColor)
        context.addPath(body)
        context.fillPath()
        context.restoreGState()
        context.setFillColor(BrandPalette.paper.cgColor)
        context.setStrokeColor(BrandPalette.ink.cgColor)
        context.setLineWidth(3)
        context.addPath(body)
        context.drawPath(using: .fillStroke)
        context.setFillColor(BrandPalette.ink.cgColor)
        context.fill(CGRect(x: 67, y: 20, width: 82, height: 36))
        let linkColor = connected ? BrandPalette.hot : BrandPalette.ink
        line(context, CGPoint(x: 62, y: 23), CGPoint(x: 62, y: 48), color: linkColor, width: 3)
        line(context, CGPoint(x: 154, y: 23), CGPoint(x: 154, y: 48), color: linkColor, width: 3)
        context.fill(CGRect(x: 35, y: 36, width: 9, height: 30))
        context.fill(CGRect(x: 25, y: 46, width: 29, height: 9))
        for rect in [CGRect(x: 68, y: 64, width: 24, height: 24), CGRect(x: 124, y: 64, width: 24, height: 24)] {
            context.fillEllipse(in: rect)
        }
        context.setStrokeColor(BrandPalette.ink.cgColor)
        context.setLineWidth(3)
        context.strokeEllipse(in: CGRect(x: 65, y: 61, width: 30, height: 30))
        context.strokeEllipse(in: CGRect(x: 121, y: 61, width: 30, height: 30))
        for point in [(173.0,33.0), (185.0,46.0), (173.0,59.0), (161.0,46.0)] {
            context.fillEllipse(in: CGRect(x: point.0, y: point.1, width: 10, height: 10))
        }
        for index in 0..<5 {
            let offset = CGFloat(index) * 4
            line(context, CGPoint(x: 20 + offset, y: 88), CGPoint(x: 16 + offset, y: 107), width: 1.2)
            line(context, CGPoint(x: 174 + offset, y: 88), CGPoint(x: 178 + offset, y: 107), width: 1.2)
        }
    }
}

final class BrandHeaderView: NSView {
    var tagline: String { didSet { updateAccessibility(); needsDisplay = true } }
    // Supply the full localized version label; the view adds no translated copy.
    var version: String { didSet { updateAccessibility(); needsDisplay = true } }
    override var isFlipped: Bool { true }
    override var isOpaque: Bool { true }
    override var intrinsicContentSize: NSSize { NSSize(width: NSView.noIntrinsicMetric, height: 106) }

    init(tagline: String, version: String) {
        self.tagline = tagline
        self.version = version
        super.init(frame: NSRect(x: 0, y: 0, width: 852, height: 106))
        setAccessibilityElement(true)
        setAccessibilityRole(.image)
        updateAccessibility()
    }
    required init?(coder: NSCoder) { fatalError("Use init(tagline:version:)") }

    private func updateAccessibility() {
        setAccessibilityLabel(["MadSense", tagline, "BLUETOOTH / macOS", version].filter { !$0.isEmpty }.joined(separator: ". "))
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        BrandPalette.paper.setFill()
        bounds.fill()
        context.setShouldAntialias(true)
        let width = bounds.width
        context.saveGState()
        context.translateBy(x: 5, y: 10)
        context.rotate(by: -.pi / 30)
        context.setFillColor(BrandPalette.hot.cgColor)
        context.fill(CGRect(x: 1, y: 1, width: 68, height: 68))
        BrandDrawing.logo(context, in: CGRect(x: 5, y: 4, width: 58, height: 58))
        context.restoreGState()
        let textRight = width > 650 ? width - 161 : width
        // AppKit's Impact glyphs sit closer to their drawing origin than GDI's.
        // Leave an optical gutter after the rotated logo, including its top corner.
        BrandDrawing.text("MADSENSE", in: CGRect(x: 96, y: 0, width: textRight - 96, height: 70),
                          color: BrandPalette.ink, largest: 57, smallest: 40, display: true)
        BrandDrawing.text(tagline, in: CGRect(x: 100, y: 65, width: textRight - 100, height: 26),
                          color: BrandPalette.ink, largest: 10.5, smallest: 8, wrap: true)
        BrandDrawing.line(context, CGPoint(x: 0, y: 94), CGPoint(x: width, y: 94), width: 3)
        if width > 650 {
            let x = width - 146
            context.setFillColor(BrandPalette.ink.cgColor)
            context.fill(CGRect(x: x, y: 12, width: 146, height: 76))
            BrandDrawing.text("BLUETOOTH / macOS", in: CGRect(x: x + 11, y: 20, width: 125, height: 18),
                              color: BrandPalette.paper, largest: 10.5, smallest: 9)
            context.setFillColor(BrandPalette.acid.cgColor)
            context.fill(CGRect(x: x + 10, y: 42, width: 126, height: 25))
            BrandDrawing.text(version, in: CGRect(x: x + 16, y: 45, width: 114, height: 22),
                              color: BrandPalette.ink, largest: 13, smallest: 9)
            // Keep the printed barcode inside the badge, clear of the header rule.
            context.setFillColor(BrandPalette.paper.cgColor)
            context.fill(CGRect(x: x + 10, y: 72, width: 126, height: 11))
            context.setFillColor(BrandPalette.ink.cgColor)
            for index in 0..<12 {
                context.fill(CGRect(x: x + 15 + CGFloat(index) * 10, y: 74,
                                    width: index % 3 == 0 ? 6 : 2, height: 6))
            }
        }
    }
}

// The artwork portion of HeroPanel. The caller composes the real LINK/detail and
// telemetry controls below it, so native selectable status text remains usable.
final class BrandHeroView: NSView {
    var headline = "" { didSet { changed() } }
    var subheadline = "" { didSet { changed() } }
    var badge = "" { didSet { changed() } }
    var mode = "" { didSet { changed() } }
    var connected = false { didSet { changed() } }
    override var isFlipped: Bool { true }
    override var isOpaque: Bool { true }
    override var intrinsicContentSize: NSSize { NSSize(width: NSView.noIntrinsicMetric, height: 250) }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setAccessibilityElement(true)
        setAccessibilityRole(.image)
        changed()
    }
    convenience init() { self.init(frame: NSRect(x: 0, y: 0, width: 852, height: 250)) }
    required init?(coder: NSCoder) { fatalError("Use init(frame:)") }

    private func changed() {
        setAccessibilityLabel([mode, headline, subheadline, badge].filter { !$0.isEmpty }.joined(separator: ". "))
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        let width = bounds.width, height = bounds.height
        context.setFillColor(BrandPalette.ink.cgColor)
        context.fill(bounds)
        guard width >= 20, height >= 20 else { return }
        context.setShouldAntialias(true)
        context.saveGState()
        context.clip(to: bounds.insetBy(dx: 2, dy: 2))
        BrandDrawing.polygon(context, [(width * 0.62,-5),(width,-5),(width,height + 2),(width * 0.49,height + 2)], BrandPalette.hot)
        context.setFillColor(BrandPalette.ink.withAlphaComponent(75 / 255).cgColor)
        for y in stride(from: CGFloat(9), to: height, by: 11) {
            for x in stride(from: width * 0.62, to: width, by: 11) {
                context.fillEllipse(in: CGRect(x: x + (Int(y) % 2 == 0 ? 3 : 0), y: y, width: 3, height: 3))
            }
        }
        for index in 0..<5 {
            let y = CGFloat(45 + index * 22), offset = CGFloat(index) * 5
            BrandDrawing.line(context, CGPoint(x: width * 0.65 + offset, y: y),
                              CGPoint(x: width * 0.9 + offset, y: y - 15), color: BrandPalette.paper, width: 2.2)
        }
        let controllerWidth = min(290, max(220, width * 0.34))
        context.saveGState()
        context.translateBy(x: width * 0.76, y: height * 0.51)
        context.rotate(by: -11 * .pi / 180)
        BrandDrawing.controller(context, in: CGRect(x: -controllerWidth / 2, y: -controllerWidth * 0.3,
                                                   width: controllerWidth, height: controllerWidth * 0.6), connected: connected)
        context.restoreGState()
        let headlineWidth = width * 0.51 - 34
        BrandDrawing.text(mode, in: CGRect(x: 23, y: 20, width: headlineWidth, height: 20),
                          color: BrandPalette.paper, largest: 10.5, smallest: 9)
        BrandDrawing.text(headline, in: CGRect(x: 20, y: 45, width: headlineWidth, height: 68),
                          color: BrandPalette.paper, largest: 55, smallest: 28, display: true)
        BrandDrawing.text(subheadline, in: CGRect(x: 23, y: 112, width: headlineWidth, height: 45),
                          color: BrandPalette.paper, largest: 35, smallest: 22, display: true)
        if !badge.isEmpty {
            let labelY = min(height - 40, max(164, height - 45))
            let measured = (badge as NSString).size(withAttributes: [.font: BrandPalette.monoFont(10.5)]).width
            let badgeWidth = max(0, min(headlineWidth - 12, max(212, measured + 40)))
            BrandDrawing.polygon(context, [(25,labelY),(25 + badgeWidth + 6,labelY - 3),
                                          (25 + badgeWidth,labelY + 28),(22,labelY + 30)],
                                 connected ? BrandPalette.acid : BrandPalette.paper)
            BrandDrawing.text(badge, in: CGRect(x: 37, y: labelY + 7, width: badgeWidth - 38, height: 18),
                              color: BrandPalette.ink, largest: 10.5, smallest: 9)
            // Printed cursor only: it never implies activity or a connection.
            context.setFillColor(BrandPalette.ink.cgColor)
            context.fill(CGRect(x: 25 + badgeWidth - 18, y: labelY + 10, width: 6, height: 6))
        }
        context.restoreGState()
        context.setStrokeColor(BrandPalette.ink.cgColor)
        context.setLineWidth(3)
        context.stroke(bounds.insetBy(dx: 1.5, dy: 1.5))
    }
}

// NSButton retains target/action, keyboard activation, focus and accessibility;
// only its printed appearance is replaced. Selection is supplied by the caller.
final class BrandButton: NSButton {
    var selected = false { didSet { state = selected ? .on : .off; needsDisplay = true } }
    var accent = false { didSet { needsDisplay = true } }
    private var hovered = false
    private var hoverTracking: NSTrackingArea?
    override var isFlipped: Bool { true }
    override var acceptsFirstResponder: Bool { isEnabled }
    override var intrinsicContentSize: NSSize {
        let textWidth = (title as NSString).size(withAttributes: [.font: BrandPalette.monoFont(12)]).width
        return NSSize(width: max(80, ceil(textWidth) + 28), height: 44)
    }

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        setButtonType(.momentaryPushIn)
        isBordered = false
        focusRingType = .none
        font = BrandPalette.monoFont(12)
        heightAnchor.constraint(greaterThanOrEqualToConstant: 44).isActive = true
    }
    convenience init(title: String, target: AnyObject?, action: Selector?) {
        self.init(frame: .zero)
        self.title = title
        self.target = target
        self.action = action
    }
    required init?(coder: NSCoder) { fatalError("Use init(title:target:action:)") }

    override func updateTrackingAreas() {
        if let tracking = hoverTracking { removeTrackingArea(tracking) }
        let tracking = NSTrackingArea(rect: .zero, options: [.mouseEnteredAndExited, .activeInKeyWindow, .inVisibleRect], owner: self, userInfo: nil)
        addTrackingArea(tracking)
        hoverTracking = tracking
        super.updateTrackingAreas()
    }
    override func mouseEntered(with event: NSEvent) { hovered = true; needsDisplay = true }
    override func mouseExited(with event: NSEvent) { hovered = false; needsDisplay = true }
    override func resetCursorRects() {
        if isEnabled { addCursorRect(bounds, cursor: .pointingHand) }
    }
    override func highlight(_ flag: Bool) { super.highlight(flag); needsDisplay = true }
    override func becomeFirstResponder() -> Bool {
        let result = super.becomeFirstResponder()
        needsDisplay = true
        return result
    }
    override func resignFirstResponder() -> Bool {
        let result = super.resignFirstResponder()
        needsDisplay = true
        return result
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let context = NSGraphicsContext.current?.cgContext else { return }
        BrandPalette.paper.setFill()
        bounds.fill()
        guard bounds.width >= 8, bounds.height >= 8 else { return }
        var face = CGRect(x: 1, y: 1, width: bounds.width - 6, height: bounds.height - 6)
        context.setFillColor(BrandPalette.ink.cgColor)
        context.fill(face.offsetBy(dx: 4, dy: 4))
        if isEnabled && (cell?.isHighlighted ?? false) { face = face.offsetBy(dx: 2, dy: 2) }
        let fill = !isEnabled ? BrandPalette.disabled : selected ? BrandPalette.acid : accent ? BrandPalette.hot : hovered ? NSColor.white : BrandPalette.paper
        context.setFillColor(fill.cgColor)
        context.fill(face)
        context.setStrokeColor(BrandPalette.ink.cgColor)
        context.setLineWidth(2)
        context.stroke(face)
        BrandDrawing.text(title, in: face.insetBy(dx: 10, dy: 4), color: isEnabled ? BrandPalette.ink : BrandPalette.muted,
                          largest: 12, smallest: 9, centered: true)
        if window?.firstResponder === self && isEnabled {
            context.saveGState()
            context.setLineWidth(1)
            context.setLineDash(phase: 0, lengths: [2, 2])
            context.stroke(face.insetBy(dx: 6, dy: 6))
            context.restoreGState()
        }
    }
}
