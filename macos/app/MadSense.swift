import AppKit
import Foundation
import Darwin

// The backend alone decides whether the bridge is usable. A running child process,
// an open physical controller or an available library never implies "Ready".
private enum Palette {
    static let paper = NSColor(srgbRed: 0.96, green: 0.95, blue: 0.90, alpha: 1)
    static let ink = NSColor(srgbRed: 0.094, green: 0.094, blue: 0.082, alpha: 1)
    static let orange = NSColor(srgbRed: 0.976, green: 0.329, blue: 0.157, alpha: 1)
    static let lime = NSColor(srgbRed: 0.863, green: 0.945, blue: 0.192, alpha: 1)
    static let muted = NSColor(srgbRed: 0.38, green: 0.38, blue: 0.35, alpha: 1)
}

private let translations: [String: [String: String]] = {
    guard let url = Bundle.main.url(forResource: "Localizations", withExtension: "json"),
          let data = try? Data(contentsOf: url),
          let dictionary = try? JSONSerialization.jsonObject(with: data) as? [String: [String: String]] else { return [:] }
    return dictionary
}()
private var language: String {
    let selected = UserDefaults.standard.string(forKey: "MadSenseLanguage") ?? Locale.preferredLanguages.first ?? "en"
    return String(selected.prefix(2))
}
private func tr(_ english: String, _ spanishText: String) -> String {
    language == "es" ? spanishText : translations[english]?[language] ?? english
}

private final class BrandView: NSView {
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        Palette.orange.setFill()
        bounds.fill()
        let scale = min(bounds.width, bounds.height) / 64
        func polygon(_ points: [(CGFloat, CGFloat)], _ color: NSColor) {
            let path = NSBezierPath()
            guard let first = points.first else { return }
            path.move(to: NSPoint(x: first.0 * scale, y: first.1 * scale))
            for p in points.dropFirst() { path.line(to: NSPoint(x: p.0 * scale, y: p.1 * scale)) }
            path.close()
            color.setFill()
            path.fill()
        }
        // The same paths as src/assets/madsense.svg; no raster dependency.
        polygon([(3,51),(13,10),(27,26),(36,8),(47,24),(58,11),(59,52),(46,52),(46,34),(36,48),(25,35),(17,54)], Palette.ink)
        polygon([(23,23),(34,15),(29,31),(40,28),(29,47),(31,34),(20,37)], Palette.lime)
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

private final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var window: NSWindow!
    private var statusItem: NSStatusItem!
    private var stateLabel = label(tr("Starting", "Iniciando"), size: 30, weight: .bold)
    private var detailLabel = label(tr("Opening the controller bridge…", "Abriendo el puente del mando…"), size: 14)
    private var telemetryLabel = label("—", size: 12, color: Palette.muted)
    private var capabilityLabel = label("", size: 12, color: Palette.muted)
    private var pauseButton: NSButton!
    private var pauseMenuItem: NSMenuItem!
    private var process: Process?
    private var input: FileHandle?
    private var outputPipe: Pipe?
    private var errorPipe: Pipe?
    private var pendingOutput = Data()
    private var session = UUID()
    private var quitting = false
    private var terminationReplied = false
    private var paused = false
    private var restarts = 0
    private var recovering = false
    private var log = ""
    private var logWindow: NSWindow?
    private var logView: NSTextView?
    private var lastStatusTime = Date()
    private var healthTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildWindow()
        buildMenu()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startBackend()
        if CommandLine.arguments.contains("--smoke-test") {
            // Exercise the real startup/shutdown path in a graphical CI session.
            // There is no mock controller and no fabricated connected state.
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { NSApp.terminate(nil) }
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

    private func buildWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 660, height: 600),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "MadSense · macOS"
        window.backgroundColor = Palette.paper
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        let root = NSView()
        window.contentView = root
        let brand = BrandView()
        brand.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([brand.widthAnchor.constraint(equalToConstant: 68), brand.heightAnchor.constraint(equalToConstant: 68)])
        let wordmark = label("MADSENSE", size: 34, weight: .black)
        let subtitle = label(tr("DUALSENSE, ON YOUR TERMS.", "TU DUALSENSE. A TU MANERA."), size: 11, weight: .semibold)
        let titles = NSStackView(views: [wordmark, subtitle])
        titles.orientation = .vertical
        titles.alignment = .leading
        titles.spacing = 5
        let header = NSStackView(views: [brand, titles])
        header.orientation = .horizontal
        header.spacing = 18
        header.alignment = .centerY
        let badge = label(tr("macOS · DEVELOPMENT BUILD", "macOS · VERSIÓN EN DESARROLLO"), size: 10, weight: .bold, color: Palette.muted)
        let rule = NSBox()
        rule.boxType = .separator
        pauseButton = NSButton(title: tr("Pause bridge", "Pausar puente"), target: self, action: #selector(togglePause))
        pauseButton.bezelStyle = .rounded
        pauseButton.controlSize = .large
        let diagnosticButton = NSButton(title: tr("Diagnostics", "Diagnóstico"), target: self, action: #selector(showDiagnostics))
        diagnosticButton.bezelStyle = .rounded
        diagnosticButton.controlSize = .large
        let buttons = NSStackView(views: [pauseButton, diagnosticButton])
        buttons.orientation = .horizontal
        buttons.spacing = 12
        let limitation = label(tr(
            "The optional CoreAudio audio/PCM endpoint is experimental. Game association and hardware compatibility are unverified. XInput is unsupported. Virtual HID requires Apple-authorized signing.",
            "El dispositivo de audio/PCM CoreAudio es opcional y experimental. La asociación con juegos y la compatibilidad con hardware no están verificadas. XInput no está soportado. El HID virtual requiere una firma autorizada por Apple."), size: 12, color: Palette.muted)
        let stack = NSStackView(views: [header, badge, rule, stateLabel, detailLabel, telemetryLabel, capabilityLabel, buttons, limitation])
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 16
        stack.setCustomSpacing(4, after: stateLabel)
        stack.setCustomSpacing(6, after: telemetryLabel)
        stack.translatesAutoresizingMaskIntoConstraints = false
        root.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 30),
            stack.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -30),
            stack.topAnchor.constraint(equalTo: root.topAnchor, constant: 26),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: root.bottomAnchor, constant: -22),
            detailLabel.widthAnchor.constraint(equalTo: stack.widthAnchor),
            capabilityLabel.widthAnchor.constraint(equalTo: stack.widthAnchor),
            limitation.widthAnchor.constraint(equalTo: stack.widthAnchor),
            rule.widthAnchor.constraint(equalTo: stack.widthAnchor)
        ])
    }

    private func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: tr("Show MadSense", "Mostrar MadSense"), action: #selector(showWindow), keyEquivalent: "0").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: tr("Quit MadSense", "Salir de MadSense"), action: #selector(quit), keyEquivalent: "q").target = self
        appItem.submenu = appMenu
        NSApp.mainMenu = main
        if let existing = statusItem { NSStatusBar.system.removeStatusItem(existing) }
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "M·S"
        statusItem.button?.toolTip = "MadSense"
        let menu = NSMenu()
        menu.addItem(withTitle: tr("Show MadSense", "Mostrar MadSense"), action: #selector(showWindow), keyEquivalent: "").target = self
        pauseMenuItem = menu.addItem(withTitle: tr("Pause bridge", "Pausar puente"), action: #selector(togglePause), keyEquivalent: "")
        pauseMenuItem.target = self
        menu.addItem(withTitle: tr("Diagnostics", "Diagnóstico"), action: #selector(showDiagnostics), keyEquivalent: "").target = self
        let languages = NSMenu()
        for (code, title) in [("en", "English"), ("es", "Español"), ("fr", "Français"),
                              ("de", "Deutsch"), ("it", "Italiano"), ("pt", "Português Brasileiro")] {
            let item = languages.addItem(withTitle: title, action: #selector(changeLanguage(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = code
            item.state = language == code ? .on : .off
        }
        let languageItem = menu.addItem(withTitle: tr("Language", "Idioma"), action: nil, keyEquivalent: "")
        languageItem.submenu = languages
        menu.addItem(.separator())
        menu.addItem(withTitle: tr("Quit MadSense", "Salir de MadSense"), action: #selector(quit), keyEquivalent: "").target = self
        statusItem.menu = menu
    }

    private func startBackend() {
        guard !quitting, process?.isRunning != true else { return }
        let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/backend/MadSense.Backend")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            setState(tr("Incomplete installation", "Instalación incompleta"), detail: tr("The bundled controller bridge is missing.", "Falta el puente del mando en la aplicación."), ready: false)
            appendLog("Missing executable: \(executable.path)\n")
            return
        }
        let child = Process()
        let stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
        let token = UUID()
        session = token
        recovering = false
        pendingOutput.removeAll(keepingCapacity: true)
        child.executableURL = executable
        child.arguments = ["--service"]
        child.currentDirectoryURL = executable.deletingLastPathComponent()
        child.standardInput = stdin
        child.standardOutput = stdout
        child.standardError = stderr
        input = stdin.fileHandleForWriting
        outputPipe = stdout
        errorPipe = stderr
        stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            DispatchQueue.main.async { guard self?.session == token else { return }; self?.receive(data) }
        }
        stderr.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            let text = String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async { guard self?.session == token else { return }; self?.appendLog(text) }
        }
        child.terminationHandler = { [weak self] child in
            DispatchQueue.main.async {
                guard let self = self, self.session == token else { return }
                self.input = nil
                self.process = nil
                if self.quitting { self.finishTermination(); return }
                self.setState(tr("Bridge stopped", "Puente detenido"), detail: tr("Reconnecting…", "Reconectando…"), ready: false)
                self.appendLog("Backend exited: \(child.terminationStatus)\n")
                self.restarts += 1
                if self.restarts <= 3 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + Double(self.restarts * 2)) { [weak self] in self?.startBackend() }
                } else {
                    self.setState(tr("Bridge could not start", "El puente no pudo arrancar"),
                        detail: tr("Open diagnostics to inspect the error, then use Resume to retry.", "Abre el diagnóstico para consultar el error y pulsa Reanudar para reintentar."), ready: false)
                    self.paused = true
                    self.refreshPause()
                }
            }
        }
        do {
            process = child
            lastStatusTime = Date()
            try child.run()
            if paused { send("pause") }
        } catch {
            process = nil
            stdout.fileHandleForReading.readabilityHandler = nil
            stderr.fileHandleForReading.readabilityHandler = nil
            setState(tr("Could not start", "No se pudo arrancar"), detail: error.localizedDescription, ready: false)
            appendLog("Launch error: \(error)\n")
        }
    }

    private func receive(_ data: Data) {
        pendingOutput.append(data)
        if pendingOutput.count > 1_048_576 {
            pendingOutput.removeAll(keepingCapacity: true)
            appendLog("Discarded oversized backend status.\n")
            return
        }
        while let newline = pendingOutput.firstIndex(of: 10) {
            let line = pendingOutput[..<newline]
            pendingOutput.removeSubrange(...newline)
            guard let object = try? JSONSerialization.jsonObject(with: Data(line)),
                  let status = object as? [String: Any], let rawState = status["state"] as? String else { continue }
            lastStatusTime = Date()
            restarts = 0
            let state = rawState.lowercased()
            if state == "diagnostic" {
                if let formatted = try? JSONSerialization.data(withJSONObject: status, options: [.prettyPrinted, .sortedKeys]) {
                    appendLog(String(decoding: formatted, as: UTF8.self) + "\n")
                }
                continue
            }
            let ready = state == "ready"
            let names = ["ready": tr("Connected", "Conectado"), "waiting": tr("Waiting for controller", "Esperando al mando"),
                         "paused": tr("Paused", "En pausa"), "connecting": tr("Connecting", "Conectando"),
                         "error": tr("Needs attention", "Requiere atención"), "blocked": tr("Setup required", "Falta configuración"),
                         "starting": tr("Starting", "Iniciando"), "stopped": tr("Stopped", "Detenido")]
            setState(names[state] ?? rawState, detail: status["detail"] as? String ?? "", ready: ready)
            paused = state == "paused"
            refreshPause()
            var parts: [String] = []
            if let battery = status["battery"] as? NSNumber, battery.intValue >= 0 {
                parts.append(tr("Battery", "Batería") + ": \(battery)%")
            }
            if let reports = status["reports"] as? NSNumber { parts.append(tr("Reports", "Informes") + ": \(reports)") }
            if let version = status["version"] as? String { parts.append(version) }
            telemetryLabel.stringValue = parts.joined(separator: "  ·  ")
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
    }

    private func setState(_ state: String, detail: String, ready: Bool) {
        stateLabel.stringValue = state
        stateLabel.textColor = ready ? NSColor(srgbRed: 0.18, green: 0.42, blue: 0.12, alpha: 1) : Palette.ink
        detailLabel.stringValue = detail
        statusItem?.button?.title = ready ? "M·S ●" : "M·S"
        statusItem?.button?.toolTip = "MadSense · \(state)"
    }

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
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    private func stopBackend(_ child: Process, token: UUID) {
        guard owns(child, token: token), child.isRunning else { return }
        send("quit")
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            guard let self = self, self.owns(child, token: token), child.isRunning else { return }
            self.appendLog("Backend did not exit after quit; sending SIGTERM to its child process.\n")
            child.terminate()
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            guard let self = self, self.owns(child, token: token), child.isRunning else { return }
            // A native IOKit/CoreAudio call can block managed cancellation. Only
            // the captured, still-running child may be killed; never look up a
            // process by name or signal a newer backend session.
            let pid = child.processIdentifier
            guard pid > 0 else { return }
            self.appendLog("Backend remained blocked after SIGTERM; sending SIGKILL to child \(pid).\n")
            if Darwin.kill(pid, SIGKILL) != 0 {
                self.appendLog("SIGKILL failed for the backend child (errno \(errno)).\n")
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 7) { [weak self] in
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
        let title = paused ? tr("Resume bridge", "Reanudar puente") : tr("Pause bridge", "Pausar puente")
        pauseButton.title = title
        pauseMenuItem.title = title
    }

    private func appendLog(_ text: String) {
        log += text
        if log.count > 100_000 { log = String(log.suffix(80_000)) }
        logView?.string = log
        if let view = logView { view.scrollToEndOfDocument(nil) }
    }

    @objc private func togglePause() {
        if process?.isRunning != true {
            paused = false
            restarts = 0
            refreshPause()
            startBackend()
            return
        }
        send(paused ? "resume" : "pause")
    }

    @objc private func showWindow() { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc private func changeLanguage(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        UserDefaults.standard.set(code, forKey: "MadSenseLanguage")
        let frame = window.frame
        let visible = window.isVisible
        window.close()
        stateLabel = label(tr("Starting", "Iniciando"), size: 30, weight: .bold)
        detailLabel = label("", size: 14)
        telemetryLabel = label("—", size: 12, color: Palette.muted)
        capabilityLabel = label("", size: 12, color: Palette.muted)
        buildWindow()
        window.setFrame(frame, display: true)
        buildMenu()
        refreshPause()
        if visible { showWindow() }
        // The next real service status restores the current connection state.
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
        healthTimer?.invalidate()
        guard let child = process, child.isRunning else { return .terminateNow }
        if quitting { return .terminateLater }
        quitting = true
        stopBackend(child, token: session)
        return .terminateLater
    }
}

let app = NSApplication.shared
private let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
