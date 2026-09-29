# LiveTR3 macOS HIG and Liquid Glass Findings

Date: 2026-05-06

## Sources

- Apple HIG: Layout - https://developer.apple.com/design/human-interface-guidelines/layout
- Apple HIG: Materials - https://developer.apple.com/design/human-interface-guidelines/materials
- Apple HIG: Motion - https://developer.apple.com/design/human-interface-guidelines/motion
- Apple HIG: Typography - https://developer.apple.com/design/human-interface-guidelines/typography
- Apple HIG: Icons - https://developer.apple.com/design/human-interface-guidelines/icons
- Apple HIG: Modality - https://developer.apple.com/design/human-interface-guidelines/modality
- Apple HIG: Settings - https://developer.apple.com/design/human-interface-guidelines/settings
- Apple HIG: Layout and organization - https://developer.apple.com/design/human-interface-guidelines/layout-and-organization
- Local implementation: `macos/LiveTR3Mac/Sources/LiveTR3Mac/ContentView.swift`
- Local implementation: `macos/LiveTR3Mac/Sources/LiveTR3Mac/GlassCompatibility.swift`
- Local implementation: `macos/LiveTR3Mac/Sources/LiveTR3Mac/LiveTR3App.swift`

## Findings

1. The app already has the right macOS skeleton: `WindowGroup`, `NavigationSplitView`, source-list navigation, a toolbar action, and `@SceneStorage` for window-local selection.
2. The previous detail column used an explicit window-background layer behind every screen. That makes the layout feel more custom than native and can interfere with system material behavior, especially on newer macOS releases.
3. Sidebar content should stay source-list dense. The runtime status card in the sidebar was visually heavier than the navigation rows, so it now uses the same row grammar: one symbol, one title line, and one short secondary line.
4. The operator screen had strong custom backdrop gradients and large corner radii. Those have been reduced so the app relies more on adaptive system materials and less on custom chrome.
5. The app had a restart command, but no native Settings scene. Runtime startup and advanced endpoint visibility are now durable macOS preferences exposed through `Settings`.
6. Typography now leans on semantic system text styles instead of a large fixed custom title size, which improves Dynamic Type behavior and cross-display scaling.
7. Empty archive state now uses `ContentUnavailableView`, a better native pattern than a custom hero panel for unavailable content.
8. Non-clickable glass surfaces should not advertise interactivity. Header and hero icon glass now render as passive material; the floating runtime control group remains interactive because it contains an actual restart control.

## Changes Made

- Added `SettingsView.swift` with grouped settings for automatic runtime startup and advanced runtime details.
- Added a native `Settings` scene and toolbar `SettingsLink`.
- Used the automatic-start setting before starting the local runtime.
- Removed the custom detail-column background wrapper.
- Replaced the sidebar runtime card with a native source-list-style row.
- Reduced decorative gradients and corner radii around the embedded operator workspace.
- Reserved interactive glass for control-bearing surfaces.
- Added an optional advanced runtime details panel controlled by Settings.
- Replaced the archive placeholder with `ContentUnavailableView`.

## Remaining Opportunities

- Split `ContentView.swift` into dedicated files for sidebar, operator, runtime, projector, and archive views. The current file is functional but too large for long-term maintenance.
- Add a dedicated Commands menu item for opening the projector route when the runtime is ready.
- Revisit runtime lifetime ownership if the app adds multiple operator windows; the current `WindowGroup` still stops the shared runtime when a window disappears.
- If targeting macOS 26 as a primary design baseline, wrap related custom glass elements in `GlassEffectContainer` and add `glassEffectID` for any expanding or morphing runtime controls.
- Add visual verification screenshots on both macOS 14 fallback material and macOS 26 Liquid Glass once a macOS 26 runtime is available.
