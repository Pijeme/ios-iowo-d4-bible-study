import UIKit
import WebKit

@MainActor final class StudyMessageRouter: NSObject, WKScriptMessageHandlerWithReply {
    weak var owner: StudyViewController?
    init(_ owner: StudyViewController) { self.owner = owner }
    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard let owner, message.frameInfo.isMainFrame, owner.isTrusted(message.webView?.url),
              let body = message.body as? [String: Any], let method = body["method"] as? String,
              let args = body["args"] as? [Any] else { replyHandler(nil, "Untrusted request."); return }
        do { replyHandler(try owner.receive(method, args: args), nil) }
        catch { replyHandler(nil, error.localizedDescription) }
    }
}

@MainActor final class StudyViewController: UIViewController, WKNavigationDelegate, WKUIDelegate, UIAdaptivePresentationControllerDelegate {
    private var services: NativeServices?
    private var reader: WKWebView!
    private var splash: UIView!
    private var ready = false
    private var readerShown = false
    private var startupFailed = false
    private var artworkHoldFinished = false
    private var artworkGeneration = 0
    private var artworkHold: DispatchWorkItem?
    private let preparation = UIView()
    private let loadingLabel = UILabel()
    private let progressBar = UIProgressView(progressViewStyle: .default)
    private let verseLabel = UILabel()
    private let splashControls = UIStackView()
    private struct StudyVerse: Decodable { let reference: String; let text: String; let translation: String }
    private var studyVerses: [StudyVerse] = []
    private var verseIndex = 0
    private var verseTimer: Timer?
    private var tutorialVisible = false
    private var firstTour = false
    private var overlay: UIViewController?
    private var baseURL: URL?
    private var loadingURL: URL?
    private let engineQueue = DispatchQueue(label: "org.iowo.study.python", qos: .userInitiated)
    override var preferredStatusBarStyle: UIStatusBarStyle { .lightContent }
    override var prefersStatusBarHidden: Bool { !ready }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = UIColor(red: 10/255, green: 31/255, blue: 70/255, alpha: 1)
        reader = makeWebView(); reader.alpha = 0; reader.isUserInteractionEnabled = false
        reader.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(reader)
        NSLayoutConstraint.activate([reader.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            reader.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            reader.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            reader.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor)])
        showSplash(); startEngine()
        NotificationCenter.default.addObserver(self, selector: #selector(becameActive), name: UIApplication.didBecomeActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(goingInactive), name: UIApplication.willResignActiveNotification, object: nil)
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        if !UserDefaults.standard.bool(forKey: "tutorial-intro-seen") && overlay == nil { showTutorials(first: true) }
    }

    private func showSplash() {
        splash = UIView(); splash.backgroundColor = view.backgroundColor
        splash.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(splash)
        NSLayoutConstraint.activate([splash.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            splash.trailingAnchor.constraint(equalTo: view.trailingAnchor), splash.topAnchor.constraint(equalTo: view.topAnchor),
            splash.bottomAnchor.constraint(equalTo: view.bottomAnchor)])
        let image = UIImageView(image: UIImage(named: "SplashArtwork"))
        image.contentMode = .scaleAspectFit; image.accessibilityLabel = "IOWO D4 Bible Study"
        image.translatesAutoresizingMaskIntoConstraints = false; splash.addSubview(image)
        NSLayoutConstraint.activate([image.leadingAnchor.constraint(equalTo: splash.leadingAnchor), image.trailingAnchor.constraint(equalTo: splash.trailingAnchor),
            image.topAnchor.constraint(equalTo: splash.safeAreaLayoutGuide.topAnchor), image.bottomAnchor.constraint(equalTo: splash.safeAreaLayoutGuide.bottomAnchor)])
        preparation.backgroundColor = UIColor(red: 16/255, green: 36/255, blue: 73/255, alpha: 0.94)
        preparation.layer.cornerRadius = 16; preparation.translatesAutoresizingMaskIntoConstraints = false
        splash.addSubview(preparation)
        let stack = UIStackView(arrangedSubviews: [loadingLabel, progressBar, verseLabel])
        stack.axis = .vertical; stack.spacing = 12; stack.translatesAutoresizingMaskIntoConstraints = false
        preparation.addSubview(stack)
        loadingLabel.text = "Opening your offline study library?"; loadingLabel.textColor = .white
        loadingLabel.font = .systemFont(ofSize: 13); loadingLabel.textAlignment = .center; loadingLabel.numberOfLines = 0
        verseLabel.textColor = UIColor(red: 1, green: 247/255, blue: 223/255, alpha: 1)
        verseLabel.font = .systemFont(ofSize: 14); verseLabel.textAlignment = .center; verseLabel.numberOfLines = 0
        progressBar.progressTintColor = UIColor(red: 230/255, green: 199/255, blue: 128/255, alpha: 1)
        progressBar.progress = 0.03
        let width = preparation.widthAnchor.constraint(equalTo: splash.widthAnchor, constant: -40); width.priority = .defaultHigh
        NSLayoutConstraint.activate([preparation.topAnchor.constraint(equalTo: splash.safeAreaLayoutGuide.topAnchor, constant: 12),
            preparation.centerXAnchor.constraint(equalTo: splash.centerXAnchor), width,
            preparation.widthAnchor.constraint(lessThanOrEqualToConstant: 600),
            stack.leadingAnchor.constraint(equalTo: preparation.leadingAnchor, constant: 18),
            stack.trailingAnchor.constraint(equalTo: preparation.trailingAnchor, constant: -18),
            stack.topAnchor.constraint(equalTo: preparation.topAnchor, constant: 14),
            stack.bottomAnchor.constraint(equalTo: preparation.bottomAnchor, constant: -14)])
        splashControls.axis = .horizontal; splashControls.spacing = 10; splashControls.alignment = .center
        splashControls.translatesAutoresizingMaskIntoConstraints = false; splash.addSubview(splashControls)
        for (title, action, tutorial) in [("About Publisher", #selector(openPublisher), false), ("Tutorials", #selector(openTutorials), true)] {
            let button = UIButton(type: .system); button.setTitle(title, for: .normal)
            button.setTitleColor(tutorial ? .white : UIColor(red: 23/255, green: 46/255, blue: 78/255, alpha: 1), for: .normal)
            button.backgroundColor = tutorial ? UIColor(red: 35/255, green: 108/255, blue: 114/255, alpha: 0.94) : UIColor(red: 1, green: 251/255, blue: 240/255, alpha: 0.95)
            button.layer.cornerRadius = 24; button.layer.borderWidth = tutorial ? 0 : 1
            button.layer.borderColor = UIColor(red: 158/255, green: 189/255, blue: 202/255, alpha: 1).cgColor
            button.contentEdgeInsets = UIEdgeInsets(top: 0, left: tutorial ? 22 : 16, bottom: 0, right: tutorial ? 22 : 16)
            button.heightAnchor.constraint(equalToConstant: 50).isActive = true
            button.addTarget(self, action: action, for: .touchUpInside); splashControls.addArrangedSubview(button)
        }
        NSLayoutConstraint.activate([splashControls.centerXAnchor.constraint(equalTo: splash.centerXAnchor),
            splashControls.widthAnchor.constraint(lessThanOrEqualToConstant: 380),
            splashControls.leadingAnchor.constraint(greaterThanOrEqualTo: splash.leadingAnchor, constant: 16),
            splashControls.bottomAnchor.constraint(equalTo: splash.safeAreaLayoutGuide.bottomAnchor, constant: -20)])
        if let path = Bundle.main.url(forResource: "startup-verses", withExtension: "json", subdirectory: "Resources/branding"),
           let data = try? Data(contentsOf: path), let verses = try? JSONDecoder().decode([StudyVerse].self, from: data), !verses.isEmpty {
            studyVerses = verses; verseIndex = UserDefaults.standard.integer(forKey: "startup-verse") % verses.count
            UserDefaults.standard.set((verseIndex + 1) % verses.count, forKey: "startup-verse")
            showNextVerse(); startVerseRotation()
        }
    }

    private func showNextVerse() {
        guard !studyVerses.isEmpty else { return }
        let verse = studyVerses[verseIndex % studyVerses.count]; verseIndex += 1
        verseLabel.text = verse.text + "\n\n" + verse.reference + " (" + verse.translation + ")"
    }
    private func startVerseRotation() {
        verseTimer?.invalidate()
        guard !ready, !startupFailed, !studyVerses.isEmpty else { return }
        verseTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.showNextVerse() }
        }
    }
    private func updateProgress(_ value: Float, message: String) {
        guard !ready, !startupFailed else { return }
        progressBar.progress = max(progressBar.progress, min(1, max(0, value / 100)))
        loadingLabel.text = message
    }
    private func preparationFinished() {
        guard !ready, !startupFailed else { return }
        ready = true; verseTimer?.invalidate(); verseTimer = nil
        progressBar.progress = 1; preparation.isHidden = true; splashControls.isHidden = true
        revealReader()
    }
    private func cancelArtworkHold() {
        artworkGeneration += 1; artworkHold?.cancel(); artworkHold = nil
        if !readerShown { artworkHoldFinished = false }
    }

    private func startEngine() {
        engineQueue.async { [weak self] in
            guard let self else { return }
            do {
                let installer = try ResourceInstaller(); let native = NativeServices(installer: installer)
                native.controller = self
                DispatchQueue.main.async { self.updateProgress(15, message: "Starting the Bible Study engine") }
                var error: NSError?
                guard let result = IOWOStartEngine(Bundle.main.resourcePath!, installer.data.path,
                    { [weak native] method, payload in native?.dispatch(method, payload: payload) ?? "{\"ok\":false,\"error\":\"App unavailable\"}" }, &error) else {
                    throw error ?? NSError.study("Could not start the offline study engine.")
                }
                guard let endpoint = try JSONSerialization.jsonObject(with: Data(result.utf8)) as? [String: Any],
                      let urlText = endpoint["url"] as? String, let url = URL(string: urlText), url.host == "127.0.0.1",
                      let gate = endpoint["gate"] as? String else { throw NSError.study("Invalid local study address.") }
                DispatchQueue.main.async {
                    self.services = native; self.baseURL = url
                    self.updateProgress(40, message: "Preparing your Bible and saved notes")
                    guard let cookie = HTTPCookie(properties: [.domain: "127.0.0.1", .path: "/", .name: "iowo_device_gate",
                        .value: gate, HTTPCookiePropertyKey("HttpOnly"): "TRUE", HTTPCookiePropertyKey("SameSite"): "Strict"]) else { return }
                    self.reader.configuration.websiteDataStore.httpCookieStore.setCookie(cookie) {
                        self.loadingURL = url; self.reader.load(URLRequest(url: url))
                    }
                }
            } catch { DispatchQueue.main.async { self.showError(error.localizedDescription) } }
        }
    }

    private func makeWebView() -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        config.userContentController.addScriptMessageHandler(StudyMessageRouter(self), contentWorld: .page, name: "iowo")
        if let scriptURL = Bundle.main.url(forResource: "native-bridge", withExtension: "js", subdirectory: "Resources"),
           let script = try? String(contentsOf: scriptURL, encoding: .utf8) {
            config.userContentController.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self; web.uiDelegate = self
        web.isOpaque = false; web.backgroundColor = view.backgroundColor
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.allowsBackForwardNavigationGestures = false
        return web
    }

    func isTrusted(_ url: URL?) -> Bool {
        guard let url else { return false }
        if url.isFileURL, let resources = Bundle.main.resourceURL?.appendingPathComponent("Resources") {
            let path = url.standardizedFileURL.path
            return path.hasPrefix(resources.appendingPathComponent("tutorials").path + "/") ||
                   path.hasPrefix(resources.appendingPathComponent("about-developer").path + "/")
        }
        guard let port = baseURL?.port else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == port
    }

    func receive(_ method: String, args: [Any]) throws -> Any {
        switch method {
        case "study.progress":
            updateProgress((args.first as? NSNumber)?.floatValue ?? 0, message: args.count > 1 ? String((args[1] as? String ?? "Preparing your study tools").prefix(200)) : "Preparing your study tools")
            return NSNull()
        case "study.ready": preparationFinished(); return NSNull()
        case "study.failed": showError("The Bible or sermon notes could not be prepared. Close and reopen the app; your notes are safe."); return NSNull()
        case "notes.formatting": if args.first as? Bool == true { reader.endEditing(true) }; return NSNull()
        case "clipboard.copy":
            guard let text = args.first as? String, text.utf8.count <= 1_048_576 else { throw NSError.study("Selection is too large to copy.") }
            UIPasteboard.general.string = text; return NSNull()
        case "text.share":
            guard let text = args.first as? String, text.utf8.count <= 300_000 else { throw NSError.study("Selection is too large to share.") }
            let share = UIActivityViewController(activityItems: [text], applicationActivities: nil)
            let presenter = overlay ?? self; share.popoverPresentationController?.sourceView = presenter.view
            share.popoverPresentationController?.sourceRect = CGRect(x: presenter.view.bounds.midX, y: presenter.view.bounds.midY, width: 1, height: 1)
            presenter.present(share, animated: true); return NSNull()
        case "file.export": guard let services else { throw NSError.study("The study engine is still starting.") }; try services.exportFile(args, presenter: self); return NSNull()
        case "tutorial.open": showTutorials(first: false); return NSNull()
        case "tutorial.finish": closeOverlay(); return NSNull()
        case "publisher.open": showPublisher(); return NSNull()
        default: throw NSError.study("Unsupported app action.")
        }
    }

    private func revealReader() {
        guard ready, !startupFailed, !readerShown, overlay == nil, UIApplication.shared.applicationState == .active else { return }
        if !artworkHoldFinished {
            guard artworkHold == nil else { return }
            let generation = artworkGeneration
            let work = DispatchWorkItem { [weak self] in
                Task { @MainActor [weak self] in
                    guard let self, self.artworkGeneration == generation else { return }; self.artworkHold = nil
                    guard self.overlay == nil, UIApplication.shared.applicationState == .active, !self.startupFailed else { return }
                    self.artworkHoldFinished = true; self.revealReader()
                }
            }
            artworkHold = work; DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: work)
            return
        }
        readerShown = true
        splash.isHidden = true; reader.alpha = 1; reader.isUserInteractionEnabled = true
        setNeedsStatusBarAppearanceUpdate()
    }

    @objc private func openTutorials() { showTutorials(first: false) }
    @objc private func openPublisher() { showPublisher() }

    private func showTutorials(first: Bool) {
        guard overlay == nil else { return }
        cancelArtworkHold(); tutorialVisible = true; firstTour = first
        reader.evaluateJavaScript("window.__iowoTutorialActive=true", completionHandler: nil)
        let url = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "Resources/tutorials")!
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        if first { components.queryItems = [URLQueryItem(name: "tour", value: "1")] }
        showLocalPage(components.url!, title: "Tutorials")
    }

    private func showPublisher() {
        guard overlay == nil else { return }
        cancelArtworkHold()
        let url = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "Resources/about-developer")!
        showLocalPage(url, title: "About Publisher")
    }

    private func showLocalPage(_ url: URL, title: String) {
        let page = UIViewController(); let web = makeWebView()
        page.view = web; page.title = title
        page.navigationItem.rightBarButtonItem = UIBarButtonItem(title: "Done", style: .done, target: self, action: #selector(closeOverlay))
        let navigation = UINavigationController(rootViewController: page)
        navigation.modalPresentationStyle = .fullScreen; overlay = navigation
        navigation.presentationController?.delegate = self
        present(navigation, animated: true)
        web.loadFileURL(url, allowingReadAccessTo: Bundle.main.resourceURL!.appendingPathComponent("Resources"))
    }

    @objc private func closeOverlay() {
        overlay?.dismiss(animated: true) { [weak self] in self?.overlayClosed() }
    }

    private func overlayClosed() {
        if firstTour { UserDefaults.standard.set(true, forKey: "tutorial-intro-seen") }
        firstTour = false; tutorialVisible = false; overlay = nil
        reader.evaluateJavaScript("window.__iowoTutorialActive=false;window.dispatchEvent(new Event('iowo-tutorial-closed'))", completionHandler: nil)
        revealReader()
    }
    func presentationControllerDidDismiss(_ presentationController: UIPresentationController) { overlayClosed() }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === reader {
            webView.evaluateJavaScript("!!document.getElementById('bibleStudyRoot')") { [weak self] value, _ in
                if value as? Bool == false { self?.preparationFinished() }
            }
        } else { webView.evaluateJavaScript("window.__iowoTutorialActive=\(tutorialVisible)", completionHandler: nil) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if isTrusted(url) || url.absoluteString == "about:blank" { decisionHandler(.allow); return }
        decisionHandler(.cancel)
        if action.navigationType == .linkActivated && ["https", "mailto", "tel"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { if webView === reader { showError("Could not open the local study screen. Reopen the app; your notes were kept.") } }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { if (error as NSError).code != NSURLErrorCancelled { self.webView(webView, didFail: navigation, withError: error) } }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = UIAlertController(title: "Bible Study", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() }); (overlay ?? self).present(alert, animated: true)
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = UIAlertController(title: "Bible Study", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: "Continue", style: .default) { _ in completionHandler(true) }); (overlay ?? self).present(alert, animated: true)
    }
    private func showError(_ message: String) {
        if !readerShown { startupFailed = true; cancelArtworkHold(); verseTimer?.invalidate(); preparation.isHidden = false; splashControls.isHidden = false; loadingLabel.text = "The workspace could not open. Your saved data was kept." }
        let alert = UIAlertController(title: "Could not open Bible Study", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default)); (overlay ?? self).present(alert, animated: true)
    }
    @objc private func becameActive() {
        if ready { revealReader() } else { startVerseRotation() }
        reader?.evaluateJavaScript("window.dispatchEvent(new Event('iowo-app-resumed'))", completionHandler: nil)
    }
    @objc private func goingInactive() {
        cancelArtworkHold(); verseTimer?.invalidate()
        reader?.evaluateJavaScript("document.dispatchEvent(new Event('visibilitychange'))", completionHandler: nil)
    }
    deinit { artworkHold?.cancel(); verseTimer?.invalidate(); NotificationCenter.default.removeObserver(self) }
}
