// Draws the PixelCrew app icon (1024x1024 PNG): Claude and Codex as pixel
// characters behind a desk, on the night-office wall.
// Usage: swift make_icon.swift <out.png>
import AppKit

let rows = [
    "....................",
    "....................",
    "..........MMMMMMMMMM",
    "..HHHHHH....hhhhhh..",
    ".HHHHHHHHH.hhhhhhhh.",
    ".HSSSSSSHHdhTTTTTThd",
    ".HSKSSKSH.MhTKTTKThM",
    ".HSSSSSSH.dhTTTTTThd",
    "..SSssSS....TTttTT..",
    "...SSSS......TTTT...",
    ".OOOCCOOO..DDDDDDDD.",
    ".OOOOOOOO..DDDMDMDD.",
    ".OOOOOOOO..DDDMDMDD.",
    ".OOOOOOOO..DDDDDDDD.",
    ".OOOOOOOO..DDDDDDDD.",
    "WWWWWWWWWWWWWWWWWWWW",
    "wwwwwwwwwwwwwwwwwwww",
    "wwwwwwwwwwwwwwwwwwww",
    "wwwwwwwwwwwwwwwwwwww",
    "wwwwwwwwwwwwwwwwwwww",
]
precondition(rows.allSatisfy { $0.count == 20 }, "every row must be 20 cells")

func rgb(_ hex: UInt32) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}

let palette: [Character: NSColor] = [
    "H": rgb(0x6B3A2A), "S": rgb(0xF5CDAA), "s": rgb(0xDDA985), "K": rgb(0x111113),
    "O": rgb(0xFFA552), "C": rgb(0xFBE3C4),
    "h": rgb(0x19191C), "T": rgb(0xC98E6A), "t": rgb(0xA87252), "M": rgb(0x4FE0B6),
    "D": rgb(0x34406A), "d": rgb(0x1E1E22),
    "W": rgb(0x9A7759), "w": rgb(0x5C4433),
]

let size = 1024
let image = NSImage(size: NSSize(width: size, height: size), flipped: true) { _ in
    let inset: CGFloat = 100
    let side = CGFloat(size) - inset * 2
    let shape = NSBezierPath(roundedRect: NSRect(x: inset, y: inset, width: side, height: side), xRadius: 185, yRadius: 185)

    NSGraphicsContext.current?.saveGraphicsState()
    shape.addClip()
    rgb(0x1B1B1E).setFill()
    shape.fill()
    let cell = side / 20
    rgb(0x202024).setFill()
    for col in stride(from: 0, to: 20, by: 2) {
        NSRect(x: inset + CGFloat(col) * cell, y: inset, width: cell, height: cell * 15).fill()
    }
    for (r, row) in rows.enumerated() {
        for (c, ch) in row.enumerated() {
            guard let color = palette[ch] else { continue }
            color.setFill()
            let x0 = (inset + CGFloat(c) * cell).rounded(), x1 = (inset + CGFloat(c + 1) * cell).rounded()
            let y0 = (inset + CGFloat(r) * cell).rounded(), y1 = (inset + CGFloat(r + 1) * cell).rounded()
            NSRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0).fill()
        }
    }
    // desk edge highlight and grain
    rgb(0xB8916B).setFill()
    NSRect(x: inset, y: (inset + 15 * cell).rounded(), width: side, height: (cell / 4).rounded()).fill()
    rgb(0x3F2F24).setFill()
    for col in [4, 10, 16] {
        NSRect(x: (inset + CGFloat(col) * cell).rounded(), y: (inset + 16.5 * cell).rounded(),
               width: (cell / 5).rounded(), height: (cell * 2.5).rounded()).fill()
    }
    NSGraphicsContext.current?.restoreGraphicsState()
    return true
}

guard CommandLine.arguments.count > 1,
      let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fatalError("usage: swift make_icon.swift <out.png>")
}
try png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
