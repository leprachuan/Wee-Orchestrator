import SwiftUI

enum WeeTheme {
    static let background = Color(red: 0.039, green: 0.055, blue: 0.102)
    static let emerald = Color(red: 0.243, green: 0.812, blue: 0.557)
    static let accent = emerald
    static let gold = Color(red: 0.961, green: 0.773, blue: 0.259)
    static let danger = Color(red: 1.0, green: 0.373, blue: 0.427)
    static let textPrimary = Color.white.opacity(0.92)
    static let textSecondary = Color.white.opacity(0.64)
    static let textMuted = Color.white.opacity(0.42)
    static let glassFill = Color(red: 0.071, green: 0.110, blue: 0.098).opacity(0.62)
    static let glassStroke = Color.white.opacity(0.13)
    static let sunken = Color.black.opacity(0.28)
}

struct WeeBackground: View {
    var body: some View {
        ZStack {
            WeeTheme.background.ignoresSafeArea()

            RadialGradient(
                colors: [Color(red: 0.051, green: 0.290, blue: 0.165).opacity(0.72), .clear],
                center: .topLeading,
                startRadius: 20,
                endRadius: 380
            )
            .ignoresSafeArea()

            RadialGradient(
                colors: [WeeTheme.gold.opacity(0.18), .clear],
                center: .topTrailing,
                startRadius: 20,
                endRadius: 310
            )
            .ignoresSafeArea()

            RadialGradient(
                colors: [Color(red: 0.102, green: 0.165, blue: 0.424).opacity(0.56), .clear],
                center: .bottomTrailing,
                startRadius: 20,
                endRadius: 360
            )
            .ignoresSafeArea()
        }
    }
}

struct GlassPanel: ViewModifier {
    var radius: CGFloat = 16
    var fill: Color = WeeTheme.glassFill

    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(fill)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            )
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .stroke(WeeTheme.glassStroke, lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.34), radius: 22, x: 0, y: 14)
    }
}

extension View {
    func glassPanel(radius: CGFloat = 16, fill: Color = WeeTheme.glassFill) -> some View {
        modifier(GlassPanel(radius: radius, fill: fill))
    }
}

struct StatusPill: View {
    let text: String
    var color: Color = WeeTheme.accent
    var symbol: String?

    var body: some View {
        HStack(spacing: 6) {
            if let symbol {
                Image(systemName: symbol)
                    .font(.caption2.weight(.bold))
            }
            Text(text)
                .font(.caption.weight(.semibold))
                .lineLimit(1)
        }
        .foregroundStyle(color)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(color.opacity(0.13), in: Capsule())
        .overlay(Capsule().stroke(color.opacity(0.24), lineWidth: 1))
    }
}
