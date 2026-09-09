// Second Brain - a menu bar front end for the local vault server.
//
// The Python service already runs in the background (see `brain service`), so
// this app is deliberately thin: a status item, a global hotkey, and a web view
// pointed at localhost. It holds no state and does no retrieval of its own.

import Carbon.HIToolbox
import Cocoa
import WebKit

private let defaultURL = "http://127.0.0.1:8077/"
private let popoverSize = NSSize(width: 460, height: 620)

// Option+Space. Change these two to rebind; see mac/build.sh to rebuild.
private let hotKeyCode = UInt32(kVK_Space)
private let hotKeyModifiers = UInt32(optionKey)

extension Notification.Name {
    static let secondBrainHotKey = Notification.Name("secondBrainHotKey")
}

private func serverURL() -> URL {
    let raw = ProcessInfo.processInfo.environment["SECOND_BRAIN_URL"]
        ?? UserDefaults.standard.string(forKey: "serverURL")
        ?? defaultURL
    return URL(string: raw) ?? URL(string: defaultURL)!
}

/// Where the Python project lives. Baked in at build time by mac/build.sh.
private func projectPath() -> URL {
    let raw = ProcessInfo.processInfo.environment["SECOND_BRAIN_PROJECT"]
        ?? (Bundle.main.object(forInfoDictionaryKey: "SBProjectPath") as? String)
        ?? NSHomeDirectory() + "/Documents/Projects/second-brain-rag"
    return URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
}

// MARK: - Server process
//
// The app runs the Python server itself rather than leaving it to a launchd
// agent. Both the project and the vault live under ~/Documents, which macOS
// protects with TCC: a launchd agent gets "Operation not permitted" for every
// file there, no matter who owns it. A GUI app, by contrast, is prompted for
// access once and its child processes inherit that grant.

final class ServerProcess {
    private var process: Process?

    var isRunning: Bool { process?.isRunning ?? false }

    /// Ask the port whether something already answers there.
    static func probe(_ done: @escaping (Bool) -> Void) {
        var request = URLRequest(url: serverURL().appendingPathComponent("api/status"))
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { _, response, _ in
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            DispatchQueue.main.async { done(ok) }
        }.resume()
    }

    func start() {
        guard !isRunning else { return }
        let root = projectPath()
        let python = root.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            NSLog("SecondBrain: no interpreter at \(python.path)")
            return
        }

        let task = Process()
        task.executableURL = python
        task.arguments = ["-m", "app.cli", "serve", "--no-browser",
                          "--host", "127.0.0.1", "--port", "8077",
                          "--log-level", "warning"]
        task.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        task.environment = environment

        if let log = try? FileHandle(forWritingTo: logURL(root)) {
            log.seekToEndOfFile()
            task.standardOutput = log
            task.standardError = log
        }

        do {
            try task.run()
            process = task
        } catch {
            NSLog("SecondBrain: could not start server: \(error)")
        }
    }

    private func logURL(_ root: URL) -> URL {
        let url = root.appendingPathComponent("data/app-server.log")
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        return url
    }

    func stop() {
        process?.terminate()
        process = nil
    }
}

// MARK: - Chat view

final class ChatController: NSViewController, WKNavigationDelegate, WKScriptMessageHandler {
    private(set) var webView: WKWebView!

    override func loadView() {
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "retry")
        webView = WKWebView(frame: NSRect(origin: .zero, size: popoverSize), configuration: config)
        webView.navigationDelegate = self
        view = webView
    }

    func load() {
        // `webView` only exists once loadView() has run, and nothing has asked
        // for the view yet when the app warms this up at launch.
        loadViewIfNeeded()
        webView.load(URLRequest(url: serverURL(), cachePolicy: .reloadIgnoringLocalCacheData))
    }

    /// Put the cursor in the question box so the hotkey lands you ready to type.
    func focusInput() {
        guard isViewLoaded else { return }
        webView.evaluateJavaScript(
            "var i=document.getElementById('input'); if(i){i.focus();}", completionHandler: nil)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        focusInput()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        showOffline(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showOffline(error)
    }

    func userContentController(_ controller: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        if message.name == "retry" { load() }
    }

    private func showOffline(_ error: Error) {
        let html = """
        <html><head><meta charset="utf-8"><style>
        body { font: 14px -apple-system, system-ui, sans-serif; color: #ddd;
               background: #1b1b1f; margin: 0; height: 100vh;
               display: flex; align-items: center; justify-content: center; }
        .box { text-align: center; padding: 28px; max-width: 340px; }
        h2 { font-size: 16px; margin: 0 0 10px; }
        p { color: #9b9aa0; line-height: 1.55; margin: 0 0 18px; }
        code { background: #2c2c32; padding: 2px 6px; border-radius: 5px; font-size: 12px; }
        button { font: inherit; padding: 7px 15px; border-radius: 9px; border: 0;
                 background: #a48bff; color: #fff; cursor: pointer; }
        </style></head><body><div class="box">
        <h2>The vault service is not responding</h2>
        <p>Start it with <code>brain service install</code>, or check
        <code>brain service status</code> in a terminal.</p>
        <button onclick="window.webkit.messageHandlers.retry.postMessage(1)">Try again</button>
        </div></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private var statusItem: NSStatusItem!
    private let popover = NSPopover()
    private let chat = ChatController()
    private var hotKeyRef: EventHotKeyRef?
    private let server = ServerProcess()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "brain",
                                   accessibilityDescription: "Second Brain")
            button.image?.isTemplate = true
            button.action = #selector(statusItemClicked)
            button.target = self
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        popover.contentSize = popoverSize
        popover.behavior = .transient
        popover.animates = false
        popover.contentViewController = chat
        popover.delegate = self

        NotificationCenter.default.addObserver(
            self, selector: #selector(toggle), name: .secondBrainHotKey, object: nil)
        registerHotKey()

        bringUpServer()
    }

    /// Reuse a server that is already listening; otherwise start our own.
    private func bringUpServer() {
        ServerProcess.probe { [weak self] alreadyRunning in
            guard let self = self else { return }
            if !alreadyRunning { self.server.start() }
            self.waitForServer(attempt: 0)
        }
    }

    private func waitForServer(attempt: Int) {
        ServerProcess.probe { [weak self] up in
            guard let self = self else { return }
            if up {
                self.chat.load()
            } else if attempt < 60 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    self.waitForServer(attempt: attempt + 1)
                }
            } else {
                self.chat.load()   // show the offline panel
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        if let ref = hotKeyRef { UnregisterEventHotKey(ref) }
        server.stop()
    }

    // MARK: hotkey

    private func registerHotKey() {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                 eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ -> OSStatus in
            NotificationCenter.default.post(name: .secondBrainHotKey, object: nil)
            return noErr
        }, 1, &spec, nil, nil)

        let id = EventHotKeyID(signature: OSType(0x5342524E), id: 1)  // "SBRN"
        RegisterEventHotKey(hotKeyCode, hotKeyModifiers, id,
                            GetApplicationEventTarget(), 0, &hotKeyRef)
    }

    // MARK: interaction

    @objc private func statusItemClicked() {
        if NSApp.currentEvent?.type == .rightMouseUp {
            showMenu()
        } else {
            toggle()
        }
    }

    @objc private func toggle() {
        if popover.isShown {
            popover.performClose(nil)
            return
        }
        guard let button = statusItem.button else { return }
        NSApp.activate(ignoringOtherApps: true)
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        // The popover only takes keystrokes once its window is key.
        popover.contentViewController?.view.window?.makeKey()
        chat.focusInput()
    }

    private func showMenu() {
        let menu = NSMenu()
        menu.addItem(withTitle: "Open in Browser", action: #selector(openInBrowser), keyEquivalent: "")
        menu.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "Reindex Vault Now", action: #selector(reindex), keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "Quit Second Brain", action: #selector(quit), keyEquivalent: "q")
        for item in menu.items where item.action != nil { item.target = self }
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil   // restore left-click-toggles behaviour
    }

    @objc private func openInBrowser() { NSWorkspace.shared.open(serverURL()) }

    @objc private func reload() { chat.load() }

    @objc private func quit() { NSApp.terminate(nil) }

    @objc private func reindex() {
        var request = URLRequest(url: serverURL().appendingPathComponent("api/reindex"))
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        URLSession.shared.dataTask(with: request) { data, _, error in
            DispatchQueue.main.async {
                let alert = NSAlert()
                if let error = error {
                    alert.messageText = "Reindex failed"
                    alert.informativeText = error.localizedDescription
                } else if let data = data,
                          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    alert.messageText = "Vault reindexed"
                    let notes = json["notes"] as? Int ?? 0
                    let chunks = json["chunks"] as? Int ?? 0
                    alert.informativeText = "\(notes) notes, \(chunks) chunks."
                } else {
                    alert.messageText = "Reindex finished"
                }
                NSApp.activate(ignoringOtherApps: true)
                alert.runModal()
            }
        }.resume()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
