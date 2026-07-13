// Interlude window — a native macOS WKWebView popup with NO Dock icon.
//
// Run:  osascript -l JavaScript webview.js <url> [width] [height]
//
// Why this instead of a Chrome --app window / installed PWA:
//   * Accessory activation policy => the window shows but adds NO Dock icon and
//     no menu bar. A real "popup app" with zero Dock presence.
//   * Zero dependency — osascript + WebKit ship with macOS.
//   * The process owns exactly one window, so interlude.py can close it cleanly
//     by killing this PID.
//
// It exits (closing the window) when:
//   * this process is killed (interlude.py do_close), or
//   * the user closes the window (red X), or
//   * the web app runs its 3-2-1 countdown and calls window.close(), which the
//     injected shim turns into a title sentinel we poll for (WKWebView ignores
//     window.close() on the top-level frame otherwise).

function run(argv) {
  ObjC.import('Cocoa');
  ObjC.import('WebKit');

  var url = argv[0] || 'about:blank';
  var W = parseInt(argv[1], 10) || 1240;
  var H = parseInt(argv[2], 10) || 840;
  var CLOSE = '__INTERLUDE_CLOSE__';

  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);

  var rect = $.NSMakeRect(0, 0, W, H);
  var style = $.NSWindowStyleMaskTitled | $.NSWindowStyleMaskClosable |
              $.NSWindowStyleMaskMiniaturizable | $.NSWindowStyleMaskResizable;
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    rect, style, $.NSBackingStoreBuffered, false);
  win.center;
  win.setTitle('Interlude');
  win.setReleasedWhenClosed(false); // so win.isVisible stays safe to read after close

  // Turn the app's window.close() into a title we can detect from here.
  var shim = "(function(){var _c=window.close;window.close=function(){" +
             "try{document.title='" + CLOSE + "';}catch(e){}" +
             "try{_c.call(window);}catch(e){}};})();";
  var ucc = $.WKUserContentController.alloc.init;
  var us = $.WKUserScript.alloc.initWithSourceInjectionTimeForMainFrameOnly(
    $(shim), $.WKUserScriptInjectionTimeAtDocumentStart, false);
  ucc.addUserScript(us);
  var cfg = $.WKWebViewConfiguration.alloc.init;
  cfg.userContentController = ucc;

  var wv = $.WKWebView.alloc.initWithFrameConfiguration(rect, cfg);
  win.setContentView(wv);
  wv.loadRequest($.NSURLRequest.requestWithURL($.NSURL.URLWithString(url)));

  win.makeKeyAndOrderFront(null);
  app.activateIgnoringOtherApps(true);

  var rl = $.NSRunLoop.currentRunLoop;
  while (true) {
    rl.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.15));
    if (!win.isVisible) break;                       // user closed the window
    var t = wv.title;
    if (t && ObjC.unwrap(t) === CLOSE) break;        // app's countdown self-close
  }
}
