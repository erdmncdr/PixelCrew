// Draws the PixelCrew app icon (1024x1024 PNG): a ring of glossy pixel tiles in the four
// agents' colours (Claude top right, Codex bottom right, Gemini bottom left, Grok top left)
// around a 2x2 core, one tile per agent: four agents, one crew.
// Usage: swift make_icon.swift <out.png>
import AppKit

func rgb(_ hex: UInt32, _ alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: alpha)
}

func mix(_ a: UInt32, _ b: UInt32, _ t: CGFloat) -> NSColor {
    func ch(_ v: UInt32, _ shift: UInt32) -> CGFloat { CGFloat((v >> shift) & 0xFF) / 255 }
    let k = max(0, min(1, t))
    return NSColor(srgbRed: ch(a, 16) + (ch(b, 16) - ch(a, 16)) * k, green: ch(a, 8) + (ch(b, 8) - ch(a, 8)) * k,
                   blue: ch(a, 0) + (ch(b, 0) - ch(a, 0)) * k, alpha: 1)
}

// Light and deep tone per agent, in ring order (clockwise from the top).
let agents: [(UInt32, UInt32)] = [
    (0xFFBE73, 0xF2852F),   // Claude
    (0x7CF5CF, 0x22B98C),   // Codex
    (0x86ABFF, 0x3F67EA),   // Gemini
    (0xF7F7F9, 0xAEB0B8),   // Grok
]

let size: CGFloat = 1024
let body = NSRect(x: 100, y: 100, width: 824, height: 824)
let center = NSPoint(x: 512, y: 512)
let grid = 28                       // tiles across the icon body; even, so the ring is symmetric
let ringRadius: CGFloat = 252, ringWidth: CGFloat = 118
let gapDegrees: CGFloat = 12        // the breaks between agents, at 12, 3, 6 and 9 o'clock

func squircle() -> NSBezierPath { NSBezierPath(roundedRect: body, xRadius: 186, yRadius: 186) }

/// One glossy tile; `t` moves from the agent's light tone (0) to its deep tone (1).
func tile(_ rect: NSRect, _ agent: Int, _ t: CGFloat, corner: CGFloat) {
    mix(agents[agent].0, agents[agent].1, t).setFill()
    NSBezierPath(roundedRect: rect, xRadius: corner, yRadius: corner).fill()
    rgb(0xFFFFFF, 0.22).setFill()
    NSBezierPath(roundedRect: NSRect(x: rect.minX, y: rect.maxY - rect.height * 0.34, width: rect.width, height: rect.height * 0.34),
                 xRadius: corner, yRadius: corner).fill()
}

/// The agent whose arc covers `angle` (degrees, counter-clockwise from 3 o'clock), or nil in a gap.
func agent(at angle: CGFloat) -> Int? {
    var a = (90 - angle).truncatingRemainder(dividingBy: 360)
    if a < 0 { a += 360 }
    let i = Int(a / 90), local = a - CGFloat(i) * 90
    return local >= gapDegrees / 2 && local <= 90 - gapDegrees / 2 ? i : nil
}

let image = NSImage(size: NSSize(width: size, height: size), flipped: false) { _ in
    guard let ctx = NSGraphicsContext.current else { return false }
    ctx.saveGraphicsState()
    squircle().addClip()
    NSGradient(colors: [rgb(0x232327), rgb(0x0C0C0E)], atLocations: [0, 1], colorSpace: .sRGB)!.draw(in: squircle(), angle: -90)
    NSGradient(colors: [rgb(0xFFFFFF, 0.06), rgb(0xFFFFFF, 0)], atLocations: [0, 1], colorSpace: .sRGB)!
        .draw(fromCenter: center, radius: 0, toCenter: center, radius: 430, options: [])

    // tiles, under one soft shadow
    ctx.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = rgb(0x000000, 0.5); shadow.shadowBlurRadius = 30; shadow.shadowOffset = NSSize(width: 0, height: -12)
    shadow.set()
    ctx.cgContext.beginTransparencyLayer(auxiliaryInfo: nil)
    let cell = body.width / CGFloat(grid), gap: CGFloat = 4
    for gy in 0..<grid {
        for gx in 0..<grid {
            let x = body.minX + (CGFloat(gx) + 0.5) * cell, y = body.minY + (CGFloat(gy) + 0.5) * cell
            let dx = x - center.x, dy = y - center.y
            guard abs((dx * dx + dy * dy).squareRoot() - ringRadius) <= ringWidth / 2,
                  let i = agent(at: atan2(dy, dx) * 180 / .pi) else { continue }
            let t = 1 - (y - (center.y - ringRadius - ringWidth / 2)) / (2 * ringRadius + ringWidth)
            tile(NSRect(x: x - cell / 2 + gap / 2, y: y - cell / 2 + gap / 2, width: cell - gap, height: cell - gap), i, t, corner: 6)
        }
    }
    // the core: each agent's tile in the quadrant its arc sits in
    let k: CGFloat = 64, g: CGFloat = 8
    for (i, qx, qy) in [(3, -1, 0), (0, 0, 0), (2, -1, -1), (1, 0, -1)] as [(Int, CGFloat, CGFloat)] {
        tile(NSRect(x: center.x + qx * (k + g) + g / 2, y: center.y + qy * (k + g) + g / 2, width: k, height: k), i, 0.35, corner: 12)
    }
    ctx.cgContext.endTransparencyLayer()
    ctx.restoreGraphicsState()
    ctx.restoreGraphicsState()

    // glassy rim: hairline plus a sheen on the upper part
    rgb(0xFFFFFF, 0.10).setStroke()
    let rim = squircle(); rim.lineWidth = 3; rim.stroke()
    ctx.saveGraphicsState()
    squircle().addClip()
    NSGradient(colors: [rgb(0xFFFFFF, 0.09), rgb(0xFFFFFF, 0)], atLocations: [0, 1], colorSpace: .sRGB)!
        .draw(in: NSRect(x: 100, y: 604, width: 824, height: 320), angle: -90)
    ctx.restoreGraphicsState()
    return true
}

guard CommandLine.arguments.count > 1,
      let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("usage: swift make_icon.swift <out.png>")
}
try png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
