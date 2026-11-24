#!/usr/bin/env python3
"""An extension's own frame can write to the clipboard after a key press in
the page that embeds it, as Vimium's yy does in its HUD iframe, in a hidden
probe.

Build first (`./build.sh`), then `python3 Tests/extension_frame_clipboard.py`.
It runs a copy of build/Search.app under a bundle id of its own, hidden, in
a world of its own with split_view.py's harness; the copy, its WebKit
folders and the world are removed afterwards.

The content script mirrors Vimium's `yy`: on `y`, it asks the background
worker for the tab's URL (`chrome.runtime.sendMessage`), then tells its
embedded extension iframe (`hud.html`, with `allow="clipboard-read;
clipboard-write"` and no `clipboardWrite` manifest permission) to copy via
`navigator.clipboard.writeText` after a short timeout. Crossing through the
background worker and into a cross-origin iframe loses WebKit's user
activation, so WebKit refuses `writeText` with `NotAllowedError`. Checked:
- `y` resolves `writeText` and writes the page URL to the bench pasteboard;
- a second write from the same single key press is refused (`NotAllowedError`);
- an unprompted write fired by a page timer (no real user input) is refused
  (`NotAllowedError`) and leaves the pasteboard untouched.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_view as sv  # noqa: E402

sv.use("extension-frame-clipboard")
BUNDLE = "com.officecommun.search.frameclipboard"
work = Path(tempfile.mkdtemp(prefix="search-frameclipboard-"))

FILES = {
    "manifest.json": json.dumps({
        "manifest_version": 3, "name": "Yank", "version": "1.0",
        "permissions": ["tabs"],
        "background": {"service_worker": "bg.js"},
        "content_scripts": [{"matches": ["<all_urls>"], "js": ["content.js"], "run_at": "document_idle"}],
        "web_accessible_resources": [{"resources": ["hud.html", "hud.js"], "matches": ["<all_urls>"]}],
    }),
    "bg.js": """
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg && msg.handler === 'getCurrentTabUrl') {
    reply(sender.tab && sender.tab.url || '');
    return false;
  }
});
""",
    "content.js": """
const say = (k, v) => document.documentElement.setAttribute('data-' + k, v);
const host = document.createElement('div');
const shadow = host.attachShadow({ mode: 'open' });
const frame = document.createElement('iframe');
frame.allow = 'clipboard-read; clipboard-write';
frame.src = chrome.runtime.getURL('hud.html');
frame.style.cssText = 'position:fixed;right:8px;bottom:8px;width:240px;height:30px;border:0';
shadow.appendChild(frame);
document.documentElement.appendChild(host);

window.addEventListener('message', (e) => {
  if (!e.data || typeof e.data !== 'object') return;
  if (e.data.ready) say('ready', '1');
  if (e.data.copyResult) say('copy-' + e.data.tag, e.data.copyResult);
});

window.addEventListener('keydown', (e) => {
  if (e.key === 'y') {
    e.preventDefault();
    chrome.runtime.sendMessage({ handler: 'getCurrentTabUrl' }, (url) => {
      frame.contentWindow.postMessage({ name: 'copy', tag: 'y', data: url || location.href }, '*');
    });
  } else if (e.key === 'd') {
    e.preventDefault();
    chrome.runtime.sendMessage({ handler: 'getCurrentTabUrl' }, () => {
      frame.contentWindow.postMessage({ name: 'double', tag: 'd', first: 'first-copy', second: 'second-copy' }, '*');
    });
  } else if (e.key === 'i') {
    // Wait past the gesture window, then attempt an unprompted write with no
    // evaluateJavaScript (which grants WebKit user activation) involved.
    e.preventDefault();
    setTimeout(() => {
      frame.contentWindow.postMessage({ name: 'copy', tag: 'idle', data: 'unprompted-copy' }, '*');
    }, 2600);
  }
}, true);
""",
    "hud.html": "<!doctype html><body style='margin:0'><script src=hud.js></script></body>",
    "hud.js": """
parent.postMessage({ ready: true }, '*');
window.addEventListener('message', (e) => {
  const msg = e.data;
  if (!msg || typeof msg !== 'object') return;
  if (msg.name === 'copy') {
    setTimeout(async () => {
      globalThis.focus();
      try {
        await navigator.clipboard.writeText(msg.data);
        parent.postMessage({ copyResult: 'ok', tag: msg.tag }, '*');
      } catch (err) {
        parent.postMessage({ copyResult: (err && err.name ? err.name : 'Error') + ': ' + (err && err.message || err), tag: msg.tag }, '*');
      }
    }, 17);
  } else if (msg.name === 'double') {
    setTimeout(async () => {
      globalThis.focus();
      let r1 = 'ok', r2 = 'ok';
      try { await navigator.clipboard.writeText(msg.first); } catch (err) { r1 = err && err.name || 'Error'; }
      try { await navigator.clipboard.writeText(msg.second); } catch (err) { r2 = err && err.name || 'Error'; }
      parent.postMessage({ copyResult: r1 + '|' + r2, tag: msg.tag }, '*');
    }, 17);
  }
});
""",
}


def probe_copy():
    app = work / "Search.app"
    shutil.copytree(Path(sv.ROOT, "build", "Search.app"), app, symlinks=True)
    subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Set :CFBundleIdentifier {BUNDLE}", str(app / "Contents/Info.plist")], check=True)
    (app / "Contents/embedded.provisionprofile").unlink(missing_ok=True)
    subprocess.run(["codesign", "-f", "-s", "-", "--entitlements", str(Path(sv.ROOT, "Search.entitlements")), "--deep", str(app)],
                   check=True, capture_output=True)
    return str(app)


def press(code, ch):
    sv.cmd({"do": "press", "code": code, "chars": ch, "mods": []})
    time.sleep(0.35)


def attr(page, name):
    return sv.cmd({"do": "eval", "id": page, "js": f"document.documentElement.getAttribute('data-{name}') || ''"}).get("value")


def wait_attr(page, name, tries=40):
    for _ in range(tries):
        val = attr(page, name)
        if val:
            return val
        time.sleep(0.15)
    return ""


def has_keys(page):
    tab = next((x for x in sv.sp("state")["tabs"] if x["id"] == page), {})
    return tab.get("hasKeys") is True


def clipboard(clear=False):
    req = {"do": "ext-clipboard", "clear": True} if clear else {"do": "ext-clipboard"}
    try:
        return sv.cmd(req)
    except RuntimeError as e:
        return {"error": str(e)}


def main():
    t = sv.T()
    folder = work / "ext"
    folder.mkdir()
    for name, body in FILES.items():
        (folder / name).write_text(body)
    sv.APP = probe_copy()
    try:
        sv.setup()
        sv.launch()
        sv.cmd({"do": "ext-answer", "answer": "yes"})
        sv.cmd({"do": "ext-folder", "path": str(folder), "yes": True})
        ext = None
        for _ in range(60):
            ext = next((x for x in sv.cmd({"do": "extensions"})["extensions"] if x["name"] == "Yank" and x["loaded"]), None)
            if ext:
                break
            time.sleep(0.25)
        t.ok("the extension loads", ext is not None)
        if not ext:
            return t

        clipboard(clear=True)
        page = sv.page("site")
        t.ok("the embedded HUD frame loads", wait_attr(page, "ready") == "1")
        page_url = sv.cmd({"do": "eval", "id": page, "js": "location.href"}).get("value")
        sv.cmd({"do": "click", "id": page, "selector": "body"})
        time.sleep(0.4)
        if not has_keys(page):
            sv.cmd({"do": "key", "id": page, "text": ""})
        t.ok("the page has the keys", has_keys(page))

        # 1. Press `y`: background round-trip + embedded iframe writeText.
        press(16, "y")
        got = wait_attr(page, "copy-y")
        t.ok("y resolves writeText from the embedded frame", got == "ok", got)
        board = clipboard()
        t.ok("y writes the page URL to the bench pasteboard", board.get("text") == page_url, board)
        t.ok("normal tab copy is not marked transient", board.get("transient") is False, board)

        # 2. A second write from a single key press is refused.
        press(2, "d")
        got_double = wait_attr(page, "copy-d")
        t.ok("one key press allows only one fallback write", got_double == "ok|NotAllowedError", got_double)
        board = clipboard()
        t.ok("pasteboard holds only the first write", board.get("text") == "first-copy", board)

        # 3. A write after the gesture window expires is refused and leaves pasteboard untouched.
        press(34, "i")
        time.sleep(2.9)
        got_idle = wait_attr(page, "copy-idle")
        t.ok("stale/unprompted writeText is refused with NotAllowedError", got_idle.startswith("NotAllowedError"), got_idle)
        board = clipboard()
        t.ok("pasteboard stays untouched after refused write", board.get("text") == "first-copy", board)
    finally:
        sv.finish()
        for kind in ("WebKit", "Caches", "HTTPStorages"):
            shutil.rmtree(Path.home() / "Library" / kind / BUNDLE, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    return t


if __name__ == "__main__":
    t = main()
    t.done()
    sys.exit(1 if t.failed else 0)
