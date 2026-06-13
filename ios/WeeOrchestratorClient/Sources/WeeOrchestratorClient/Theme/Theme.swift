import SwiftUI

/// Colors mirror the default "Emerald" glassmorphism theme from the
/// Wee Orchestrator WebUI (webui/dist/app.css and themes.css).
enum WeeTheme {
    static let accent = Color(red: 0x3e / 255, green: 0xcf / 255, blue: 0x8e / 255)
    static let danger = Color(red: 0xff / 255, green: 0x6b / 255, blue: 0x8a / 255)
    static let background = Color(red: 0x0a / 255, green: 0x0e / 255, blue: 0x1a / 255)
    static let surfaceRaised = Color.white.opacity(0.08)
    static let glassBorder = Color(red: 62 / 255, green: 207 / 255, blue: 142 / 255).opacity(0.14)
    static let textPrimary = Color.white.opacity(0.92)
    static let textSecondary = Color.white.opacity(0.58)
    static let textMuted = Color.white.opacity(0.35)
}

/// A translucent "glass card" container matching the WebUI's `.glass-card`
/// / `.glass-panel` styling.
struct GlassCard<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        content
            .padding(16)
            .background(.ultraThinMaterial)
            .background(Color.white.opacity(0.03))
            .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(WeeTheme.glassBorder, lineWidth: 1)
            )
    }
}

/// Small colored status indicator dot, mirroring `.status-dot` in the WebUI.
struct StatusDot: View {
    let isActive: Bool

    var body: some View {
        Circle()
            .fill(isActive ? WeeTheme.accent : WeeTheme.danger)
            .frame(width: 8, height: 8)
            .shadow(color: (isActive ? WeeTheme.accent : WeeTheme.danger).opacity(0.6), radius: 4)
    }
}

/// Background view with subtle gradient blobs, mirroring `.bg-blobs` in the WebUI.
struct WeeBackground: View {
    var body: some View {
        ZStack {
            WeeTheme.background.ignoresSafeArea()

            RadialGradient(
                colors: [WeeTheme.accent.opacity(0.18), .clear],
                center: .topLeading,
                startRadius: 10,
                endRadius: 400
            )
            .ignoresSafeArea()
            .blur(radius: 60)

            RadialGradient(
                colors: [Color(red: 0x6c / 255, green: 0x9f / 255, blue: 1).opacity(0.12), .clear],
                center: .bottomTrailing,
                startRadius: 10,
                endRadius: 500
            )
            .ignoresSafeArea()
            .blur(radius: 80)
        }
    }
}

extension View {
    /// Applies the standard Wee dark background behind a view's content.
    func weeBackground() -> some View {
        background(WeeBackground())
    }
}
