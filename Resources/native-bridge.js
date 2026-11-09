/* Only the trusted local main frame receives this compatibility facade. */
(function () {
    "use strict";
    const call = (method, args = []) => window.webkit.messageHandlers.iowo.postMessage({method, args});
    window.__iowoTutorialActive = false;
    window.IOWOAndroid = Object.freeze({
        tutorialActive: () => window.__iowoTutorialActive,
        openTutorials: () => call("tutorial.open"),
        setNotesFormatting: value => call("notes.formatting", [Boolean(value)]),
        studyProgress: (percent, message) => call("study.progress", [percent, message]),
        studyReady: () => call("study.ready"),
        studyFailed: message => call("study.failed", [message]),
        copyText: text => call("clipboard.copy", [String(text)]),
        shareText: text => call("text.share", [String(text)]),
        exportFile: (...args) => call("file.export", args)
    });
    window.IOWOTutorial = Object.freeze({finish: () => call("tutorial.finish"), copyText: text => call("clipboard.copy", [String(text)])});
    document.addEventListener("click", event => {
        if (event.target.closest("#mobilePublisher")) { event.preventDefault(); call("publisher.open"); }
    }, true);
})();
