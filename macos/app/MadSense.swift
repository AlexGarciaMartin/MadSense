import AppKit
import Foundation
import Darwin
import ServiceManagement

// The backend alone decides whether the bridge is usable. A running child process,
// an open physical controller or an available library never implies "Ready".
private typealias Palette = BrandPalette

// Package the original Windows UI catalogs, so the brand copy has one source.
// Mac-specific operations/errors still use Localizations.json below.
private let brandCatalogs: [String: [String: String]] = {
    var catalogs: [String: [String: String]] = [:]
    for code in ["en", "es", "fr", "de", "it", "pt-BR"] {
        guard let url = Bundle.main.url(forResource: "ui.\(code)", withExtension: "json", subdirectory: "WindowsUI"),
              let data = try? Data(contentsOf: url),
              let catalog = try? JSONSerialization.jsonObject(with: data) as? [String: String] else { continue }
        catalogs[code] = catalog
    }
    return catalogs
}()
private func brandText(_ key: String) -> String {
    brandCatalogs[language == "pt" ? "pt-BR" : language]?[key] ?? brandCatalogs["en"]?[key] ?? key
}

private let translations: [String: [String: String]] = {
    guard let url = Bundle.main.url(forResource: "Localizations", withExtension: "json"),
          let data = try? Data(contentsOf: url),
          let dictionary = try? JSONSerialization.jsonObject(with: data) as? [String: [String: String]] else { return [:] }
    return dictionary
}()
private var language: String {
    if CommandLine.arguments.contains("--smoke-test"),
       let requested = ProcessInfo.processInfo.environment["MADSENSE_SMOKE_LANGUAGE"],
       ["en", "es", "fr", "de", "it", "pt"].contains(requested) {
        return requested
    }
    let selected = UserDefaults.standard.string(forKey: "MadSenseLanguage") ?? Locale.preferredLanguages.first ?? "en"
    return String(selected.prefix(2))
}
private func tr(_ english: String, _ spanishText: String) -> String {
    language == "es" ? spanishText : translations[english]?[language] ?? english
}

private let backendDetailKeys: Set<String> = [
    "Pair a DualSense in Bluetooth settings, then press PS.",
    "Waiting for a paired Bluetooth DualSense.",
    "Controller released.",
    "Controller disconnected. Waiting to reconnect.",
    "Waiting for the first controller input report.",
    "USB controller detected. Use it natively, or unplug it to bridge Bluetooth.",
    "HID bridge active. Dedicated four-channel CoreAudio capture available; game association requires validation.",
    "HID bridge active. The optional CoreAudio endpoint is unavailable. XInput is not supported on macOS."
]
private func localizeBackendDetail(_ detail: String) -> String {
    // Exact stable protocol messages only. Native errors and diagnostic text
    // retain their original spelling, codes and context for troubleshooting.
    guard backendDetailKeys.contains(detail) else { return detail }
    return translations[detail]?[language] ?? detail
}

private func localizeBackendState(_ state: String) -> String {
    switch state.lowercased() {
    case "ready": return tr("Connected", "Conectado")
    case "waiting": return tr("Waiting for controller", "Esperando al mando")
    case "paused": return tr("Paused", "En pausa")
    case "connecting": return tr("Connecting", "Conectando")
    case "error": return tr("Needs attention", "Requiere atención")
    case "blocked": return tr("Setup required", "Falta configuración")
    case "starting": return tr("Starting", "Iniciando")
    case "stopped": return tr("Stopped", "Detenido")
    default: return state
    }
}

private func smokeLog(_ message: String) {
    guard CommandLine.arguments.contains("--smoke-test") else { return }
    let line = "MadSense GUI smoke [\(ISO8601DateFormatter().string(from: Date()))]: \(message)\n"
    try? FileHandle.standardError.write(contentsOf: Data(line.utf8))
}

private func onMainRunLoop(_ action: @escaping () -> Void) {
    // AppKit's terminateLater runs NSModalPanelRunLoopMode. Main dispatch-queue
    // blocks cannot re-enter their own serial queue, but CF run-loop blocks can
    // run in that nested loop. Include the modal mode explicitly, not by guess.
    let modes = [RunLoop.Mode.common.rawValue, RunLoop.Mode.modalPanel.rawValue] as CFArray
    CFRunLoopPerformBlock(CFRunLoopGetMain(), modes, action)
    CFRunLoopWakeUp(CFRunLoopGetMain())
}

private func afterOnMainRunLoop(_ delay: TimeInterval, _ action: @escaping () -> Void) {
    precondition(Thread.isMainThread)
    let timer = Timer(timeInterval: delay, repeats: false) { _ in action() }
    RunLoop.main.add(timer, forMode: .common)
    RunLoop.main.add(timer, forMode: .modalPanel)
}

private final class BackendOutputBuffer {
    // Each child's pipe has its own framing buffer, including trailing data
    // delivered after another child has already replaced it.
    var data = Data()
    var sawStatus = false
}

private final class AppContentView: NSView {
    override var isOpaque: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        // NSWindow normally paints behind a transparent content view, but that
        // background is absent when caching only the view. Paint the same actual
        // window color here so the live UI and its bitmap have identical backing.
        (window?.backgroundColor ?? Palette.paper).setFill()
        bounds.fill()
    }
    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        needsDisplay = true
    }
}

private func label(_ text: String, size: CGFloat, weight: NSFont.Weight = .regular,
                   color: NSColor = Palette.ink) -> NSTextField {
    let field = NSTextField(wrappingLabelWithString: text)
    field.font = NSFont.systemFont(ofSize: size, weight: weight)
    field.textColor = color
    field.isSelectable = true
    field.lineBreakMode = .byWordWrapping
    return field
}

private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate {
    private var window: NSWindow!
    private var statusItem: NSStatusItem!
    private var stateLabel = label(tr("Starting", "Iniciando"), size: 14, weight: .bold)
    private var hero = BrandHeroView()
    private var presentedBackendState: String?
    private var detailLabel = label(tr("Opening the controller bridge…", "Abriendo el puente del mando…"), size: 14)
    private var telemetryLabel = label("—", size: 12, color: Palette.muted)
    private var lowBatteryLabel = label("", size: 12, weight: .semibold,
        color: NSColor(srgbRed: 0.52, green: 0.27, blue: 0.02, alpha: 1))
    private var capabilityLabel = label("", size: 12, color: Palette.muted)
    private var activityLabel = label("", size: 12, color: Palette.muted)
    private var outputActivity: [String: Bool] = [:]
    private var batteryPercent: Int?
    private var charging: Bool?
    private var reportCount: NSNumber?
    private var backendVersion: String?
    private var displayedState: (() -> String)?
    private var displayedDetail: (() -> String)?
    private var displayedReady = false
    private var pauseButton: NSButton!
    private var pauseMenuItem: NSMenuItem!
    private var activityMenuItem: NSMenuItem!
    private var loginMenuItem: NSMenuItem!
    private var loginSettingsMenuItem: NSMenuItem!
    private var speakerButtons: [String: BrandButton] = [:]
    private var speakerMenuItems: [String: NSMenuItem] = [:]
    private var confirmedSpeaker: String?
    private var process: Process?
    private var input: FileHandle?
    private var outputPipe: Pipe?
    private var errorPipe: Pipe?
    private var session = UUID()
    private var scheduledRestart: UUID?
    private var quitting = false
    private var terminationReplied = false
    private var paused = false
    private var requestedPaused = false
    private var smokePausedRestartOriginalSession: UUID?
    private var blocked = false
    private var restarts = 0
    private var healthySince: TimeInterval?
    private var lastHealthyReport: TimeInterval?
    private var recovering = false
    private var log = ""
    private var logWindow: NSWindow?
    private var logView: NSTextView?
    private var lastStatusTime = Date()
    private var healthTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        smokeLog("applicationDidFinishLaunching")
        buildWindow()
        buildMenu()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        // Explicit visual fixtures inspect translated error/pause layouts without
        // a controller. They never start the backend and cannot prove HID works.
        if CommandLine.arguments.contains("--visual-fixture") {
            guard CommandLine.arguments.contains("--smoke-test"),
                  let fixture = ProcessInfo.processInfo.environment["MADSENSE_VISUAL_FIXTURE"],
                  ["paused", "blocked"].contains(fixture) else { Darwin.exit(2) }
            paused = fixture == "paused"
            requestedPaused = paused
            blocked = fixture == "blocked"
            if blocked { window.setContentSize(window.contentMinSize) }
            setState(localizeBackendState(fixture),
                detail: fixture == "paused" ? localizeBackendDetail("Controller released.") :
                    "Virtual HID unavailable (0xe00002bc): macOS refused virtual HID creation. Check the executable's accepted com.apple.developer.hid.virtual.device entitlement/signature.",
                ready: false, backendState: fixture)
            refreshPause()
            smokeLog("visual fixture only: \(fixture); no backend or hardware")
            afterOnMainRunLoop(0.5) { [weak self] in
                self?.captureSmokeSnapshot()
                NSApp.terminate(nil)
            }
            return
        }
        startBackend()
        smokeLog("window visible; backend startup requested")
        if CommandLine.arguments.contains("--smoke-test") {
            // Exercise the real startup/shutdown path in a graphical CI session.
            // There is no mock controller and no fabricated connected state.
            DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
                smokeLog("+4 seconds: capturing live content view")
                self?.captureSmokeSnapshot()
            }
            if ProcessInfo.processInfo.environment["MADSENSE_SMOKE_STALL_BACKEND"] == "1" {
                let token = session
                afterOnMainRunLoop(6) { [weak self] in
                    guard let self = self, let child = self.process,
                          self.owns(child, token: token), child.isRunning, !self.quitting else {
                        smokeLog("stall test failed: original backend child is not running")
                        return
                    }
                    let pid = child.processIdentifier
                    guard pid > 0 else { return }
                    if Darwin.kill(pid, SIGSTOP) == 0 {
                        smokeLog("stall test: sent SIGSTOP to owned backend child \(pid)")
                    } else {
                        smokeLog("stall test: SIGSTOP failed (errno \(errno))")
                    }
                }
            }
            if ProcessInfo.processInfo.environment["MADSENSE_SMOKE_PAUSED_RESTART"] == "1" {
                let token = session
                afterOnMainRunLoop(1) { [weak self] in
                    guard let self = self, self.session == token, self.process?.isRunning == true else { return }
                    self.togglePause()
                    smokeLog("pause restart test: requested pause through the UI action")
                }
                afterOnMainRunLoop(3) { [weak self] in
                    guard let self = self, let child = self.process,
                          self.owns(child, token: token), child.isRunning,
                          self.requestedPaused, self.paused else {
                        smokeLog("pause restart test failed: backend did not confirm pause")
                        return
                    }
                    if Darwin.kill(child.processIdentifier, SIGTERM) == 0 {
                        self.smokePausedRestartOriginalSession = token
                        smokeLog("pause restart test: sent SIGTERM to the paused owned backend")
                    } else {
                        smokeLog("pause restart test failed: SIGTERM errno \(errno)")
                    }
                }
            }
            // Deliberately originate this request on GCD's main queue: the
            // cleanup must still run in AppKit's nested modal termination loop.
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) {
                smokeLog("+8 seconds: requesting normal application termination")
                NSApp.terminate(nil)
            }
        }
        healthTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            guard let self = self, self.process?.isRunning == true, !self.quitting else { return }
            if Date().timeIntervalSince(self.lastStatusTime) > 10 {
                self.setState(tr("No response", "Sin respuesta"),
                    detail: tr("The controller bridge stopped reporting its state. Open diagnostics for details.",
                               "El puente del mando ha dejado de informar de su estado. Consulta el diagnóstico."), ready: false)
            }
            if Date().timeIntervalSince(self.lastStatusTime) > 15, !self.recovering {
                self.recovering = true
                self.appendLog("Backend status timed out; requesting a restart.\n")
                if let child = self.process { self.stopBackend(child, token: self.session) }
            }
        }
    }

    private func captureSmokeSnapshot() {
        guard let path = ProcessInfo.processInfo.environment["MADSENSE_SMOKE_SCREENSHOT"], !path.isEmpty else {
            smokeLog("snapshot skipped: MADSENSE_SMOKE_SCREENSHOT is unset")
            return
        }
        guard let view = window?.contentView else {
            smokeLog("snapshot failed: the native window has no content view")
            return
        }
        // Render this application's own live NSView. This never reads another
        // window or the display and does not require Screen Recording permission.
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
            smokeLog("snapshot failed: could not create the native bitmap")
            return
        }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else {
            smokeLog("snapshot failed: PNG encoding failed")
            return
        }
        do {
            try png.write(to: URL(fileURLWithPath: path), options: .atomic)
            smokeLog("snapshot saved: \(path) (\(bitmap.pixelsWide)x\(bitmap.pixelsHigh))")
        } catch {
            smokeLog("snapshot failed: \(error)")
        }
    }

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 900, height: 730),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.contentMinSize = NSSize(width: 800, height: 730)
        window.title = "MadSense · macOS"
        window.backgroundColor = Palette.paper
        window.appearance = NSAppearance(named: .aqua)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        let root = AppContentView()
        window.contentView = root
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
        let header = BrandHeaderView(tagline: brandText("Ui.HeaderTagline"),
            version: brandText("Ui.Version").replacingOccurrences(of: "{0}", with: version + " DEV"))
        hero = BrandHeroView()
        hero.mode = brandText("Ui.HeroDualSense")
        header.heightAnchor.constraint(equalToConstant: 106).isActive = true
        hero.heightAnchor.constraint(greaterThanOrEqualToConstant: 230).isActive = true
        hero.setContentHuggingPriority(NSLayoutConstraint.Priority(rawValue: 200), for: .vertical)

        // Actual backend state remains native, selectable and accessible in the
        // Windows LINK strip; the artwork never invents a connected controller.
        stateLabel.maximumNumberOfLines = 1
        detailLabel.maximumNumberOfLines = 2
        detailLabel.lineBreakMode = .byWordWrapping
        let statusText = NSStackView(views: [stateLabel, detailLabel, telemetryLabel, lowBatteryLabel])
        statusText.orientation = .vertical
        statusText.alignment = .leading
        statusText.spacing = 3
        let link = label("LINK", size: 12, weight: .bold)
        link.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .bold)
        let divider = NSBox()
        divider.boxType = .custom
        divider.fillColor = Palette.ink
        divider.borderType = .noBorder
        divider.widthAnchor.constraint(equalToConstant: 3).isActive = true
        divider.heightAnchor.constraint(equalToConstant: 48).isActive = true
        let statusRow = NSStackView(views: [link, divider, statusText])
        statusRow.orientation = .horizontal
        statusRow.alignment = .centerY
        statusRow.spacing = 16
        statusRow.edgeInsets = NSEdgeInsets(top: 10, left: 16, bottom: 10, right: 16)
        statusRow.wantsLayer = true
        statusRow.layer?.borderColor = Palette.ink.cgColor
        statusRow.layer?.borderWidth = 3
        statusRow.heightAnchor.constraint(greaterThanOrEqualToConstant: 78).isActive = true
        link.widthAnchor.constraint(equalToConstant: 38).isActive = true
        statusText.widthAnchor.constraint(equalTo: statusRow.widthAnchor, constant: -105).isActive = true
        detailLabel.widthAnchor.constraint(equalTo: statusText.widthAnchor).isActive = true
        lowBatteryLabel.isHidden = true

        let speakerSection = label(brandText("Ui.SpeakerMenu").uppercased(), size: 11, weight: .bold)
        speakerSection.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .bold)
        speakerButtons.removeAll()
        let speakerRow = NSStackView()
        speakerRow.orientation = .horizontal
        speakerRow.distribution = .fillEqually
        speakerRow.spacing = 12
        for (mode, title) in speakerChoices() {
            let button = BrandButton(title: title.uppercased(), target: self, action: #selector(speakerButtonChanged(_:)))
            button.identifier = NSUserInterfaceItemIdentifier(mode)
            button.setAccessibilityLabel(brandText("Ui.SpeakerLevel").replacingOccurrences(of: "{0}", with: title))
            speakerButtons[mode] = button
            speakerRow.addArrangedSubview(button)
        }
        speakerRow.heightAnchor.constraint(equalToConstant: 50).isActive = true
        refreshSpeaker()

        pauseButton = BrandButton(title: brandText("Ui.Pause"), target: self, action: #selector(togglePause))
        (pauseButton as? BrandButton)?.accent = true
        let diagnosticButton = BrandButton(title: brandText("Ui.Logs"), target: self, action: #selector(showDiagnostics))
        diagnosticButton.toolTip = tr("Diagnostics", "Diagnóstico")
        let footer = NSStackView(views: [activityLabel, diagnosticButton, pauseButton])
        footer.orientation = .horizontal
        footer.alignment = .centerY
        footer.spacing = 12
        diagnosticButton.widthAnchor.constraint(equalToConstant: 132).isActive = true
        pauseButton.widthAnchor.constraint(equalToConstant: 180).isActive = true
        activityLabel.widthAnchor.constraint(equalTo: footer.widthAnchor, constant: -336).isActive = true
        footer.heightAnchor.constraint(equalToConstant: 44).isActive = true
        activityLabel.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .bold)
        activityLabel.maximumNumberOfLines = 2
        activityLabel.setContentHuggingPriority(.defaultLow, for: .horizontal)

        let limitation = label(tr(
            "The optional CoreAudio audio/PCM endpoint is experimental. Game association and hardware compatibility are unverified. XInput is unsupported. Virtual HID requires Apple-authorized signing.",
            "El dispositivo de audio/PCM CoreAudio es opcional y experimental. La asociación con juegos y la compatibilidad con hardware no están verificadas. XInput no está soportado. El HID virtual requiere una firma autorizada por Apple."), size: 12, color: Palette.muted)
        let development = label(tr("macOS · DEVELOPMENT BUILD", "macOS · VERSIÓN EN DESARROLLO"), size: 10, weight: .bold, color: Palette.muted)
        development.font = NSFont.monospacedSystemFont(ofSize: 10, weight: .bold)
        let sectionRow = NSStackView(views: [speakerSection, development])
        sectionRow.orientation = .horizontal
        sectionRow.distribution = .equalSpacing
        sectionRow.spacing = 12
        let stack = NSStackView(views: [header, hero, statusRow, sectionRow, speakerRow, limitation, footer])
        stack.orientation = .vertical
        stack.distribution = .fill
        stack.alignment = .leading
        stack.spacing = 10
        stack.setCustomSpacing(0, after: hero)
        stack.setCustomSpacing(6, after: sectionRow)
        stack.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: root.topAnchor, constant: 12),
            stack.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -16)
        ])
        for view in [header, hero, statusRow, sectionRow, speakerRow, limitation, footer] {
            view.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        }
        redrawState()
    }

    private func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: brandText("Ui.Open"), action: #selector(showWindow), keyEquivalent: "0").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: brandText("Ui.Quit"), action: #selector(quit), keyEquivalent: "q").target = self
        appItem.submenu = appMenu
        NSApp.mainMenu = main
        if let existing = statusItem { NSStatusBar.system.removeStatusItem(existing) }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let url = Bundle.main.url(forResource: "MadSense", withExtension: "icns"), let icon = NSImage(contentsOf: url) {
            icon.size = NSSize(width: 18, height: 18)
            statusItem.button?.image = icon
            statusItem.button?.imagePosition = .imageLeading
        }
        statusItem.button?.toolTip = "MadSense"
        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        menu.addItem(withTitle: brandText("Ui.Open"), action: #selector(showWindow), keyEquivalent: "").target = self
        pauseMenuItem = menu.addItem(withTitle: tr("Pause bridge", "Pausar puente"), action: #selector(togglePause), keyEquivalent: "")
        pauseMenuItem.target = self
        menu.addItem(withTitle: brandText("Ui.Logs"), action: #selector(showDiagnostics), keyEquivalent: "").target = self
        activityMenuItem = menu.addItem(withTitle: "", action: nil, keyEquivalent: "")
        activityMenuItem.isEnabled = false
        refreshActivity()
        let speakerMenu = NSMenu()
        speakerMenu.autoenablesItems = false
        speakerMenuItems.removeAll()
        for (mode, title) in speakerChoices() {
            let item = speakerMenu.addItem(withTitle: title, action: #selector(speakerMenuChanged(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = mode
            speakerMenuItems[mode] = item
        }
        let speakerItem = menu.addItem(withTitle: brandText("Ui.SpeakerMenu"), action: nil, keyEquivalent: "")
        speakerItem.submenu = speakerMenu
        refreshSpeaker()
        let languages = NSMenu()
        for (code, title) in [("en", "English"), ("es", "Español"), ("fr", "Français"),
                              ("de", "Deutsch"), ("it", "Italiano"), ("pt", "Português Brasileiro")] {
            let item = languages.addItem(withTitle: title, action: #selector(changeLanguage(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = code
            item.state = language == code ? .on : .off
        }
        let languageItem = menu.addItem(withTitle: brandText("Ui.Language"), action: nil, keyEquivalent: "")
        languageItem.submenu = languages
        menu.addItem(.separator())
        loginMenuItem = menu.addItem(withTitle: tr("Launch at login", "Abrir al iniciar sesión"), action: #selector(toggleLogin), keyEquivalent: "")
        loginMenuItem.target = self
        loginSettingsMenuItem = menu.addItem(withTitle: tr("Approve in Login Items…", "Autorizar en Ítems de inicio…"), action: #selector(openLoginSettings), keyEquivalent: "")
        loginSettingsMenuItem.target = self
        refreshLogin()
        menu.addItem(.separator())
        menu.addItem(withTitle: brandText("Ui.Quit"), action: #selector(quit), keyEquivalent: "").target = self
        statusItem.menu = menu
    }

    private func startBackend() {
        guard !quitting, process?.isRunning != true else { return }
        scheduledRestart = nil
        let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/backend/MadSense.Backend")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            setState(tr("Incomplete installation", "Instalación incompleta"), detail: tr("The bundled controller bridge is missing.", "Falta el puente del mando en la aplicación."), ready: false)
            appendLog("Missing executable: \(executable.path)\n")
            refreshPause()
            return
        }
        let child = Process()
        let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        let token = UUID()
        let outputBuffer = BackendOutputBuffer()
        session = token
        recovering = false
        blocked = false
        healthySince = nil
        lastHealthyReport = nil
        confirmedSpeaker = nil
        refreshSpeaker()
        child.executableURL = executable
        child.arguments = requestedPaused ? ["--service", "--start-paused"] : ["--service"]
        child.currentDirectoryURL = executable.deletingLastPathComponent()
        child.standardInput = stdin
        child.standardOutput = stdout
        child.standardError = stderr
        input = stdin.fileHandleForWriting
        outputPipe = stdout
        errorPipe = stderr
        stdout.fileHandleForReading.readabilityHandler = { [weak self, weak child] handle in
            let data = handle.availableData
            let ended = data.isEmpty
            if ended { handle.readabilityHandler = nil }
            onMainRunLoop {
                self?.receive(data, from: child, token: token, buffer: outputBuffer, ended: ended)
            }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            let text = String(decoding: data, as: UTF8.self)
            onMainRunLoop { self?.appendLog(text) }
        }
        child.terminationHandler = { [weak self] child in
            onMainRunLoop {
                guard let self = self, self.owns(child, token: token) else { return }
                self.input = nil
                self.process = nil
                self.scheduledRestart = nil
                self.refreshSpeaker()
                smokeLog("backend child exited with status \(child.terminationStatus)")
                if self.quitting { self.finishTermination(); return }
                self.setState(tr("Bridge stopped", "Puente detenido"), detail: tr("Reconnecting…", "Reconectando…"), ready: false)
                self.appendLog("Backend exited: \(child.terminationStatus)\n")
                self.restarts += 1
                if self.restarts <= 3 {
                    let attempt = self.restarts
                    let ticket = UUID()
                    self.scheduledRestart = ticket
                    afterOnMainRunLoop(Double(attempt * 2)) { [weak self] in
                        guard let self = self, !self.quitting, self.session == token,
                              self.process == nil, self.scheduledRestart == ticket,
                              self.restarts == attempt, attempt <= 3 else { return }
                        self.startBackend()
                    }
                } else {
                    self.setState(tr("Bridge could not start", "El puente no pudo arrancar"),
                        detail: tr("Open diagnostics to inspect the error, then use Resume to retry.", "Abre el diagnóstico para consultar el error y pulsa Reanudar para reintentar."), ready: false)
                }
                self.refreshPause()
            }
        }
        do {
            process = child
            lastStatusTime = Date()
            smokeLog("launching backend; start paused=\(requestedPaused)")
            try child.run()
            refreshPause()
        } catch {
            process = nil
            stdout.fileHandleForReading.readabilityHandler = nil
            stderr.fileHandleForReading.readabilityHandler = nil
            setState(tr("Could not start", "No se pudo arrancar"), detail: error.localizedDescription, ready: false)
            appendLog("Launch error: \(error)\n")
            refreshPause()
        }
    }

    private func receive(_ data: Data, from child: Process?, token: UUID,
                         buffer: BackendOutputBuffer, ended: Bool) {
        buffer.data.append(data)
        if buffer.data.count > 1_048_576 {
            buffer.data.removeAll(keepingCapacity: true)
            appendLog("Discarded oversized backend status.\n")
            return
        }
        while let newline = buffer.data.firstIndex(of: 10) {
            let line = Data(buffer.data[..<newline])
            buffer.data.removeSubrange(...newline)
            guard let child = child, owns(child, token: token), child.isRunning else {
                appendLog("Backend tail [\(token.uuidString.prefix(8))]: " + String(decoding: line, as: UTF8.self) + "\n")
                continue
            }
            guard let object = try? JSONSerialization.jsonObject(with: Data(line)),
                  let status = object as? [String: Any], let rawState = status["state"] as? String else { continue }
            lastStatusTime = Date()
            let state = rawState.lowercased()
            if !buffer.sawStatus {
                buffer.sawStatus = true
                smokeLog("backend first status: \(state); requested pause=\(requestedPaused)")
                if let original = smokePausedRestartOriginalSession, original != token {
                    if requestedPaused && state == "paused" {
                        smokeLog("paused restart verified")
                    } else {
                        smokeLog("paused restart failed: first real status was \(state), requested pause=\(requestedPaused)")
                    }
                    smokePausedRestartOriginalSession = nil
                }
            }
            if let speaker = status["speaker"] as? String, ["high", "low", "off"].contains(speaker) {
                confirmedSpeaker = speaker
                refreshSpeaker()
            }
            if state == "diagnostic" {
                if let formatted = try? JSONSerialization.data(withJSONObject: status, options: [.prettyPrinted, .sortedKeys]) {
                    appendLog(String(decoding: formatted, as: UTF8.self) + "\n")
                }
                continue
            }
            // Fatal startup errors also emit valid JSON before exiting. Only
            // thirty seconds of consecutive healthy reports reset retry limits.
            if ["waiting", "connecting", "paused", "ready"].contains(state) {
                let now = ProcessInfo.processInfo.systemUptime
                if lastHealthyReport == nil || now - lastHealthyReport! > 3 { healthySince = now }
                lastHealthyReport = now
                if let since = healthySince, now - since >= 30 { restarts = 0 }
            } else {
                healthySince = nil
                lastHealthyReport = nil
            }
            let ready = state == "ready"
            setState(localizeBackendState(rawState), detail: localizeBackendDetail(status["detail"] as? String ?? ""), ready: ready, backendState: state)
            paused = state == "paused"
            blocked = state == "blocked"
            refreshPause()
            let percent = (status["battery"] as? NSNumber)?.intValue
            batteryPercent = percent.flatMap { (0...100).contains($0) ? $0 : nil }
            // null is unknown; neither a full battery nor a missing field means
            // charging. The backend reads the physical controller power state.
            charging = status["charging"] as? Bool
            reportCount = status["reports"] as? NSNumber
            backendVersion = status["version"] as? String
            refreshTelemetry()
            outputActivity = ready ? (status["activity"] as? [String: Bool] ?? [:]) : [:]
            refreshActivity()
            if let capabilities = status["capabilities"] as? [String: Any] {
                let labels = ["hidInput": tr("controller input", "entrada del mando"),
                              "hidFeedback": tr("HID effects", "efectos HID"),
                              "exclusiveAccess": tr("exclusive capture", "captura exclusiva"),
                              "nativeAudio": tr("CoreAudio endpoint active", "dispositivo CoreAudio activo"),
                              "nativeHaptics": tr("PCM forwarding active", "reenvío PCM activo"),
                              "xinput": "XInput"]
                let enabled = labels.keys.sorted().compactMap { key -> String? in
                    (capabilities[key] as? Bool) == true ? labels[key] : nil
                }
                capabilityLabel.stringValue = enabled.isEmpty ? "" : tr("Implemented: ", "Implementado: ") + enabled.joined(separator: ", ")
            } else if let capabilities = status["capabilities"] as? [String] {
                capabilityLabel.stringValue = capabilities.joined(separator: " · ")
            }
        }
        if ended && !buffer.data.isEmpty {
            appendLog("Backend partial tail [\(token.uuidString.prefix(8))]: " + String(decoding: buffer.data, as: UTF8.self) + "\n")
            buffer.data.removeAll()
        }
    }

    private func setState(_ state: @autoclosure @escaping () -> String,
                          detail: @autoclosure @escaping () -> String, ready: Bool, backendState: String? = nil) {
        // Retain the presentation expressions, not only their translated result.
        // Rebuilding the UI can then preserve and retranslate a local startup
        // failure even when no backend exists to emit another status message.
        presentedBackendState = backendState
        displayedState = state
        displayedDetail = detail
        displayedReady = ready
        if !ready {
            batteryPercent = nil
            charging = nil
            outputActivity.removeAll()
            refreshTelemetry()
            refreshActivity()
        }
        redrawState()
    }

    private func redrawState() {
        let title = displayedState?() ?? tr("Starting", "Iniciando")
        stateLabel.stringValue = title
        stateLabel.textColor = displayedReady ? NSColor(srgbRed: 0.18, green: 0.42, blue: 0.12, alpha: 1) : Palette.ink
        detailLabel.stringValue = displayedDetail?() ?? ""
        detailLabel.toolTip = detailLabel.stringValue
        hero.mode = brandText("Ui.HeroDualSense")
        hero.connected = displayedReady
        switch presentedBackendState {
        case "waiting":
            hero.headline = brandText("Ui.HeroPressPs")
            hero.subheadline = brandText("Ui.HeroOnController")
            hero.badge = brandText("Ui.BadgeWaiting")
        case "paused":
            hero.headline = brandText("Ui.HeroPaused")
            hero.subheadline = brandText("Ui.HeroReleased")
            hero.badge = brandText("Ui.BadgePaused")
        case "ready" where displayedReady:
            hero.headline = brandText("Ui.HeroReady")
            hero.subheadline = brandText("Ui.HeroPlay")
            hero.badge = brandText("Ui.BadgeReady")
        default:
            hero.headline = title.uppercased()
            hero.subheadline = ""
            hero.badge = tr("macOS · DEVELOPMENT BUILD", "macOS · VERSIÓN EN DESARROLLO")
        }
        refreshStatusItem()
    }

    private func refreshStatusItem() {
        var title = displayedReady ? "●" : ""
        if statusItem?.button?.image == nil { title = "MadSense" + (displayedReady ? " ●" : "") }
        var tooltip = "MadSense · \(displayedState?() ?? tr("Starting", "Iniciando"))"
        if let battery = batteryPercent {
            title += " \(battery)%"
            tooltip += " · " + tr("Battery", "Batería") + ": \(battery)%"
            if charging == true {
                title += " ⚡"
                tooltip += " · " + tr("Charging / charged", "Cargando / cargado")
            }
        }
        statusItem?.button?.title = title
        statusItem?.button?.toolTip = tooltip
    }

    private func refreshTelemetry() {
        var parts: [String] = []
        if let battery = batteryPercent {
            parts.append(tr("Battery", "Batería") + ": \(battery)%")
            if charging == true { parts.append(tr("Charging / charged", "Cargando / cargado")) }
        }
        telemetryLabel.stringValue = parts.joined(separator: "  ·  ")
        telemetryLabel.isHidden = parts.isEmpty
        let low = batteryPercent.map { $0 <= 15 } == true && charging == false
        lowBatteryLabel.stringValue = low ? tr("Low battery — connect the controller to USB.", "Batería baja — conecta el mando por USB.") : ""
        lowBatteryLabel.isHidden = !low
        refreshStatusItem()
    }

    private func refreshLogin() {
        guard loginMenuItem != nil else { return }
        let status = SMAppService.mainApp.status
        loginMenuItem.state = status == .enabled ? .on : status == .requiresApproval ? .mixed : .off
        loginMenuItem.isEnabled = !quitting
        loginSettingsMenuItem.isHidden = status != .requiresApproval
        loginSettingsMenuItem.isEnabled = !quitting
    }

    private func refreshActivity() {
        // These flags describe successfully delivered HID output. They do not
        // measure a physical actuator. Expiration/state comes from the backend.
        let effects = [("haptics", tr("Haptics", "Háptica")),
                       ("triggers", tr("Triggers", "Gatillos")),
                       ("audio", tr("Audio", "Audio")),
                       ("rumble", tr("Rumble", "Vibración"))]
        let active = effects.compactMap { key, title in outputActivity[key] == true ? title : nil }
        let title = tr("Output sent: ", "Salida enviada: ") + (active.isEmpty ? "—" : active.joined(separator: " · "))
        activityLabel.stringValue = title
        activityLabel.textColor = active.isEmpty ? Palette.muted : Palette.ink
        activityMenuItem?.title = title
    }

    func menuWillOpen(_ menu: NSMenu) { refreshLogin() }
    func applicationDidBecomeActive(_ notification: Notification) { refreshLogin() }

    @objc private func toggleLogin() {
        // This is the only registration path. Launch, status refresh and smoke
        // tests only read the OS setting; they never change login preferences.
        guard !quitting else { return }
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled || service.status == .requiresApproval {
                try service.unregister()
            } else {
                try service.register()
            }
        } catch {
            appendLog("Login setting failed: \(error)\n")
            showWindow()
            let alert = NSAlert()
            alert.messageText = tr("Could not change login setting", "No se pudo cambiar el inicio de sesión")
            alert.informativeText = error.localizedDescription
            alert.beginSheetModal(for: window)
        }
        refreshLogin()
    }

    @objc private func openLoginSettings() { SMAppService.openSystemSettingsLoginItems() }

    private func send(_ command: String) {
        guard let input = input, process?.isRunning == true else { return }
        do { try input.write(contentsOf: Data((command + "\n").utf8)) }
        catch { appendLog("Command failed: \(error)\n") }
    }

    private func owns(_ child: Process, token: UUID) -> Bool {
        session == token && process === child
    }

    private func finishTermination() {
        guard quitting, !terminationReplied, process?.isRunning != true else { return }
        terminationReplied = true
        smokeLog("backend exited; replying to application termination")
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    private func stopBackend(_ child: Process, token: UUID) {
        guard owns(child, token: token), child.isRunning else { return }
        smokeLog("sending quit to backend child \(child.processIdentifier)")
        send("quit")
        afterOnMainRunLoop(3) { [weak self] in
            guard let self = self, self.owns(child, token: token), child.isRunning else { return }
            self.appendLog("Backend did not exit after quit; sending SIGTERM to its child process.\n")
            smokeLog("backend quit grace expired; sending SIGTERM")
            child.terminate()
        }
        afterOnMainRunLoop(5) { [weak self] in
            guard let self = self, self.owns(child, token: token), child.isRunning else { return }
            // A native IOKit/CoreAudio call can block managed cancellation. Only
            // the captured, still-running child may be killed; never look up a
            // process by name or signal a newer backend session.
            let pid = child.processIdentifier
            guard pid > 0 else { return }
            self.appendLog("Backend remained blocked after SIGTERM; sending SIGKILL to child \(pid).\n")
            smokeLog("backend SIGTERM grace expired; sending SIGKILL to child \(pid)")
            if Darwin.kill(pid, SIGKILL) != 0 {
                self.appendLog("SIGKILL failed for the backend child (errno \(errno)).\n")
            }
        }
        afterOnMainRunLoop(7) { [weak self] in
            guard let self = self, self.quitting, !self.terminationReplied,
                  self.session == token, self.process == nil || self.process === child else { return }
            if !child.isRunning {
                // Normally the termination handler replies first. This covers
                // an exit whose notification was delayed or not delivered.
                self.finishTermination()
            } else if self.owns(child, token: token) {
                // SIGKILL has already been sent. If even kernel process exit
                // has not completed, do not leave the GUI waiting forever or
                // report graceful shutdown. OS termination releases HID handles.
                let message = Data("MadSense: backend did not exit after SIGKILL; GUI exits with failure.\n".utf8)
                try? FileHandle.standardError.write(contentsOf: message)
                Darwin._exit(EXIT_FAILURE)
            }
        }
    }

    private func refreshPause() {
        let title = blocked ? tr("Retry bridge", "Reintentar puente") :
            requestedPaused || (process?.isRunning != true && scheduledRestart == nil)
                ? brandText("Ui.Resume") : brandText("Ui.Pause")
        pauseButton.title = title
        pauseMenuItem.title = title
    }

    private func speakerChoices() -> [(String, String)] {
        [("high", brandText("Ui.SpeakerHigh")), ("low", brandText("Ui.SpeakerLow")), ("off", brandText("Ui.SpeakerOff"))]
    }

    private func refreshSpeaker() {
        let enabled = process?.isRunning == true && confirmedSpeaker != nil && !quitting
        for (mode, button) in speakerButtons {
            button.isEnabled = enabled
            button.selected = confirmedSpeaker == mode
            button.needsDisplay = true
        }
        for (mode, item) in speakerMenuItems {
            item.state = confirmedSpeaker == mode ? .on : .off
            item.isEnabled = enabled
        }
    }

    private func requestSpeaker(_ mode: String) {
        guard confirmedSpeaker != nil, ["high", "low", "off"].contains(mode), process?.isRunning == true else {
            refreshSpeaker()
            return
        }
        send("speaker \(mode)")
        // The backend persists and confirms the setting. Restore the previous
        // selection until that status arrives; no optimistic volume state.
        refreshSpeaker()
    }

    @objc private func speakerButtonChanged(_ sender: NSButton) {
        guard let mode = sender.identifier?.rawValue else { return }
        requestSpeaker(mode)
    }

    @objc private func speakerMenuChanged(_ sender: NSMenuItem) {
        guard let mode = sender.representedObject as? String else { return }
        requestSpeaker(mode)
    }

    private func appendLog(_ text: String) {
        log += text
        if log.count > 100_000 { log = String(log.suffix(80_000)) }
        logView?.string = log
        if let view = logView { view.scrollToEndOfDocument(nil) }
    }

    @objc private func togglePause() {
        guard !quitting else { return }
        if process?.isRunning != true {
            // Pause may be requested during the reconnect delay. Let the
            // scheduled child start safely paused instead of losing that intent.
            if scheduledRestart != nil && !requestedPaused {
                requestedPaused = true
                refreshPause()
                return
            }
            requestedPaused = false
            restarts = 0
            scheduledRestart = nil
            refreshPause()
            startBackend()
            return
        }
        requestedPaused = blocked ? false : !requestedPaused
        send(requestedPaused ? "pause" : "resume")
        refreshPause()
    }

    @objc private func showWindow() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc private func changeLanguage(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        UserDefaults.standard.set(code, forKey: "MadSenseLanguage")
        let frame = window.frame
        let visible = window.isVisible
        window.close()
        stateLabel = label(tr("Starting", "Iniciando"), size: 14, weight: .bold)
        detailLabel = label("", size: 14)
        telemetryLabel = label("—", size: 12, color: Palette.muted)
        capabilityLabel = label("", size: 12, color: Palette.muted)
        buildWindow()
        window.setFrame(frame, display: true)
        buildMenu()
        refreshPause()
        redrawState()
        refreshTelemetry()
        refreshActivity()
        logWindow?.title = "MadSense · " + tr("Diagnostics", "Diagnóstico")
        if visible { showWindow() }
    }
    @objc private func quit() { NSApp.terminate(nil) }
    @objc private func showDiagnostics() {
        send("diagnose")
        if logWindow == nil {
            let diagnostics = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 800, height: 480),
                styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            diagnostics.title = "MadSense · " + tr("Diagnostics", "Diagnóstico")
            diagnostics.isReleasedWhenClosed = false
            let scroll = NSScrollView(frame: diagnostics.contentView!.bounds)
            scroll.autoresizingMask = [.width, .height]
            scroll.hasVerticalScroller = true
            let view = NSTextView(frame: scroll.bounds)
            view.isEditable = false
            view.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
            view.autoresizingMask = [.width]
            view.isVerticallyResizable = true
            view.textContainer?.widthTracksTextView = true
            view.string = log
            scroll.documentView = view
            diagnostics.contentView?.addSubview(scroll)
            diagnostics.center()
            logView = view
            logWindow = diagnostics
        }
        logWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showWindow(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        smokeLog("applicationShouldTerminate: backend running=\(process?.isRunning == true), already quitting=\(quitting)")
        healthTimer?.invalidate()
        guard let child = process, child.isRunning else { return .terminateNow }
        if quitting { return .terminateLater }
        quitting = true
        refreshSpeaker()
        stopBackend(child, token: session)
        // Child completion and deadlines are CF/NS run-loop sources explicitly
        // available in modal mode, not GCD main-queue continuations. This keeps
        // normal Quit and system logout/shutdown waiting for the same cleanup.
        return .terminateLater
    }
    func applicationWillTerminate(_ notification: Notification) {
        smokeLog("applicationWillTerminate")
    }
}

@main
private enum MadSenseApplication {
    private static let delegate = AppDelegate()
    static func main() {
        smokeLog("creating NSApplication")
        let app = NSApplication.shared
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        smokeLog("entering AppKit run loop")
        app.run()
        smokeLog("AppKit run loop returned")
    }
}
