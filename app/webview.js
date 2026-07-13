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
// It runs the real AppKit event loop ([NSApp run]) so the web view is fully
// interactive and its content process stays healthy. A repeating NSTimer polls
// for the two exit conditions and terminates the app (closing the window):
//   * the user closes the window (red X)  ->  win.isVisible becomes false
//   * the app's 3-2-1 countdown calls window.close()  ->  the injected shim
//     sets a title sentinel we detect (WKWebView ignores window.close() on the
//     top-level frame otherwise)
// interlude.py killing this PID is the backstop for both.

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

  // Turn the app's window.close() into a title we can detect from the timer.
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
  wv.setAutoresizingMask($.NSViewWidthSizable | $.NSViewHeightSizable);
  win.setContentView(wv);
  wv.loadRequest($.NSURLRequest.requestWithURL($.NSURL.URLWithString(url)));

  win.makeKeyAndOrderFront(null);
  app.activateIgnoringOtherApps(true);

  // Poll for exit conditions from inside the AppKit run loop.
  $.NSTimer.scheduledTimerWithTimeIntervalRepeatsBlock(0.2, true, function (timer) {
    var done = !win.isVisible;                              // user closed the window
    if (!done) {
      var t = wv.title;
      if (t && ObjC.unwrap(t) === CLOSE) done = true;       // countdown self-close
    }
    if (done) {
      timer.invalidate;
      app.terminate(null);
    }
  });

  app.run;   // real event loop: interactive clicks + healthy WebKit content process
}
