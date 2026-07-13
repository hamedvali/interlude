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
  var FRONT_FILE = argv[3] || '';   // interlude.py bumps this to re-front us
  var CLOSE = '__INTERLUDE_CLOSE__';

  // Read the current front-counter value (empty string if the file is absent
  // or unreadable). interlude.py increments it when a new prompt arrives and a
  // window is already open, so we can raise ourselves instead of a no-op.
  function readFront() {
    if (!FRONT_FILE) return '';
    var s = $.NSString.stringWithContentsOfFileEncodingError(
      FRONT_FILE, $.NSUTF8StringEncoding, $());
    var v = ObjC.unwrap(s);
    return (typeof v === 'string') ? v : '';
  }

  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);

  // A minimal main menu. An accessory app shows no menu bar and no Dock icon,
  // but the menu's key equivalents are still honored — which is what makes
  // Cmd+C/V/A work when you sign in to a social site embedded on the Social page.
  var mainMenu = $.NSMenu.alloc.init;
  var appItem = $.NSMenuItem.alloc.init; mainMenu.addItem(appItem);
  var appMenu = $.NSMenu.alloc.init; appItem.setSubmenu(appMenu);
  appMenu.addItemWithTitleActionKeyEquivalent($('Quit'), 'terminate:', $('q'));
  var editItem = $.NSMenuItem.alloc.init; mainMenu.addItem(editItem);
  var editMenu = $.NSMenu.alloc.initWithTitle($('Edit')); editItem.setSubmenu(editMenu);
  editMenu.addItemWithTitleActionKeyEquivalent($('Undo'), 'undo:', $('z'));
  var redo = editMenu.addItemWithTitleActionKeyEquivalent($('Redo'), 'redo:', $('z'));
  redo.setKeyEquivalentModifierMask(
    $.NSEventModifierFlagCommand | $.NSEventModifierFlagShift);
  editMenu.addItem($.NSMenuItem.separatorItem);
  editMenu.addItemWithTitleActionKeyEquivalent($('Cut'), 'cut:', $('x'));
  editMenu.addItemWithTitleActionKeyEquivalent($('Copy'), 'copy:', $('c'));
  editMenu.addItemWithTitleActionKeyEquivalent($('Paste'), 'paste:', $('v'));
  editMenu.addItemWithTitleActionKeyEquivalent($('Select All'), 'selectAll:', $('a'));
  app.setMainMenu(mainMenu);

  var rect = $.NSMakeRect(0, 0, W, H);
  var style = $.NSWindowStyleMaskTitled | $.NSWindowStyleMaskClosable |
              $.NSWindowStyleMaskMiniaturizable | $.NSWindowStyleMaskResizable;
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    rect, style, $.NSBackingStoreBuffered, false);
  win.center;
  win.setTitle('Interlude');
  win.setReleasedWhenClosed(false); // so win.isVisible stays safe to read after close

  // Injected at document start: (1) flag that we're inside the native shell, so
  // the app can embed social sites inline; (2) turn the app's window.close()
  // into a title we can detect from the timer.
  var shim = "(function(){window.__ilNative=true;var _c=window.close;window.close=function(){" +
             "try{document.title='" + CLOSE + "';}catch(e){}" +
             "try{_c.call(window);}catch(e){}};})();";
  var ucc = $.WKUserContentController.alloc.init;
  var us = $.WKUserScript.alloc.initWithSourceInjectionTimeForMainFrameOnly(
    $(shim), $.WKUserScriptInjectionTimeAtDocumentStart, false);
  ucc.addUserScript(us);
  var cfg = $.WKWebViewConfiguration.alloc.init;
  cfg.userContentController = ucc;

  // A plain container holds the app web view plus (later) the social overlay as
  // siblings, so the overlay composites cleanly on top of the app.
  var container = $.NSView.alloc.initWithFrame(rect);
  win.setContentView(container);

  var wv = $.WKWebView.alloc.initWithFrameConfiguration(rect, cfg);
  wv.setAutoresizingMask($.NSViewWidthSizable | $.NSViewHeightSizable);
  container.addSubview(wv);
  wv.loadRequest($.NSURLRequest.requestWithURL($.NSURL.URLWithString(url)));

  // ---- inline social embedding ----------------------------------------------
  // The Social page can't use <iframe> (Instagram/X/TikTok forbid it), so we
  // overlay a second, real web view exactly over the page's stage element. It's
  // a top-level load (never blocked) and shares the default persistent cookie
  // store, so a login sticks across sessions. The page publishes what to show
  // via window.__ilSocial(); we poll it from the run loop below.
  var SITES = {
    instagram: 'https://www.instagram.com/',
    x: 'https://x.com/',
    tiktok: 'https://www.tiktok.com/',
  };
  var social = { view: null, site: null, shown: false };
  function ensureSocialView() {
    if (social.view) return social.view;
    var cfg2 = $.WKWebViewConfiguration.alloc.init;
    cfg2.websiteDataStore = $.WKWebsiteDataStore.defaultDataStore; // persistent login
    var ov = $.WKWebView.alloc.initWithFrameConfiguration($.NSMakeRect(0, 0, 10, 10), cfg2);
    ov.setHidden(true);
    container.addSubview(ov); // last subview => on top of the app web view
    social.view = ov;
    return ov;
  }
  function applySocial(st) {
    if (!st || !st.site || !st.visible || !SITES[st.site]) {
      if (social.view && social.shown) {
        social.shown = false;
        social.view.setHidden(true);
        // Best-effort: stop any playing media when the overlay is hidden.
        social.view.evaluateJavaScriptCompletionHandler(
          $("(function(){try{document.querySelectorAll('video,audio').forEach(function(m){m.pause();});}catch(e){}})()"),
          function (r, e) {});
      }
      return;
    }
    var ov = ensureSocialView();
    if (st.site !== social.site) {
      social.site = st.site;
      ov.loadRequest($.NSURLRequest.requestWithURL($.NSURL.URLWithString(SITES[st.site])));
    }
    var viewH = wv.frame.size.height;              // container == app web view size
    var ny = viewH - (st.y + st.h);                // web is top-left; AppKit is bottom-left
    ov.setFrame($.NSMakeRect(st.x, ny, st.w, st.h));
    if (!social.shown) { social.shown = true; ov.setHidden(false); }
  }

  win.makeKeyAndOrderFront(null);
  app.activateIgnoringOtherApps(true);

  // Baseline so the value present at launch doesn't trigger an immediate raise.
  var lastFront = readFront();

  // Poll for exit conditions + re-front requests from inside the run loop.
  $.NSTimer.scheduledTimerWithTimeIntervalRepeatsBlock(0.2, true, function (timer) {
    var done = !win.isVisible;                              // user closed the window
    if (!done) {
      var t = wv.title;
      if (t && ObjC.unwrap(t) === CLOSE) done = true;       // countdown self-close
    }
    if (done) {
      timer.invalidate;
      app.terminate(null);
      return;
    }
    var cur = readFront();                                  // new prompt while open?
    if (cur !== '' && cur !== lastFront) {
      lastFront = cur;
      win.makeKeyAndOrderFront(null);
      app.activateIgnoringOtherApps(true);
    }
    // Ask the page what (if anything) the Social page wants embedded, and where.
    wv.evaluateJavaScriptCompletionHandler(
      $("(function(){try{return (window.__ilSocial&&window.__ilSocial())||'';}catch(e){return '';}})()"),
      function (res, err) {
        var s = ObjC.unwrap(res);
        if (typeof s !== 'string' || !s) { applySocial(null); return; }
        var st = null; try { st = JSON.parse(s); } catch (e) { st = null; }
        applySocial(st);
      });
  });

  app.run;   // real event loop: interactive clicks + healthy WebKit content process
}
