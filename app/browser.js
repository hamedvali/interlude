// Interlude social browser — a standalone, signed-in web window.
//
//   osascript -l JavaScript browser.js <url> [title] [width] [height] [front-file]
//
// Opened by server.py when you click a platform on the Social page. Unlike the
// Interlude popup (webview.js), this window is a real little browser, because
// Instagram / X / TikTok can't be embedded and signing in needs a keyboard:
//
//   * REGULAR activation policy with a real Edit menu, so Cmd+C/V/A and full
//     text entry work while you log in (an accessory app has no menu → no paste).
//   * Loads the site at the TOP LEVEL. X-Frame-Options / CSP frame-ancestors
//     only block <iframe> embedding — a top-level load is never blocked.
//   * Shares the default *persistent* WKWebsiteDataStore, so your cookies and
//     login survive across windows and across restarts — you stay signed in.
//
// It terminates when you close the window (red X). server.py tracks its PID so a
// second click on the same platform re-fronts this window (via the polled
// front-file) instead of spawning a duplicate.

function run(argv) {
  ObjC.import('Cocoa');
  ObjC.import('WebKit');

  var url = argv[0] || 'about:blank';
  var title = argv[1] || 'Interlude';
  var W = parseInt(argv[2], 10) || 1040;
  var H = parseInt(argv[3], 10) || 880;
  var FRONT_FILE = argv[4] || '';

  // Current value of the re-front counter (empty if the file is absent). server.py
  // bumps it when you click an already-open platform, so we can raise ourselves.
  function readFront() {
    if (!FRONT_FILE) return '';
    var s = $.NSString.stringWithContentsOfFileEncodingError(
      FRONT_FILE, $.NSUTF8StringEncoding, $());
    var v = ObjC.unwrap(s);
    return (typeof v === 'string') ? v : '';
  }

  var app = $.NSApplication.sharedApplication;
  // Regular policy: a Dock icon + menu bar. That menu is what makes copy/paste
  // and full keyboard entry work while you sign in.
  app.setActivationPolicy($.NSApplicationActivationPolicyRegular);

  // ---- minimal main menu: standard Edit shortcuts + back/forward/reload ----
  var mainMenu = $.NSMenu.alloc.init;

  var appItem = $.NSMenuItem.alloc.init; mainMenu.addItem(appItem);
  var appMenu = $.NSMenu.alloc.init; appItem.setSubmenu(appMenu);
  appMenu.addItemWithTitleActionKeyEquivalent($('Hide'), 'hide:', $('h'));
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

  var navItem = $.NSMenuItem.alloc.init; mainMenu.addItem(navItem);
  var navMenu = $.NSMenu.alloc.initWithTitle($('History')); navItem.setSubmenu(navMenu);
  navMenu.addItemWithTitleActionKeyEquivalent($('Back'), 'goBack:', $('['));
  navMenu.addItemWithTitleActionKeyEquivalent($('Forward'), 'goForward:', $(']'));
  navMenu.addItemWithTitleActionKeyEquivalent($('Reload'), 'reload:', $('r'));

  app.setMainMenu(mainMenu);

  var rect = $.NSMakeRect(0, 0, W, H);
  var style = $.NSWindowStyleMaskTitled | $.NSWindowStyleMaskClosable |
              $.NSWindowStyleMaskMiniaturizable | $.NSWindowStyleMaskResizable;
  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer(
    rect, style, $.NSBackingStoreBuffered, false);
  win.center;
  win.setTitle(title);
  win.setReleasedWhenClosed(false); // keep win.isVisible readable after close

  var cfg = $.WKWebViewConfiguration.alloc.init;
  // Explicitly the default *persistent* store so logins/cookies persist to disk.
  cfg.websiteDataStore = $.WKWebsiteDataStore.defaultDataStore;

  var wv = $.WKWebView.alloc.initWithFrameConfiguration(rect, cfg);
  wv.setAutoresizingMask($.NSViewWidthSizable | $.NSViewHeightSizable);
  win.setContentView(wv);
  wv.loadRequest($.NSURLRequest.requestWithURL($.NSURL.URLWithString(url)));

  win.makeKeyAndOrderFront(null);
  app.activateIgnoringOtherApps(true);

  var lastFront = readFront();

  // Poll for the exit condition + re-front requests from inside the run loop.
  $.NSTimer.scheduledTimerWithTimeIntervalRepeatsBlock(0.2, true, function (timer) {
    if (!win.isVisible) {            // user closed the window
      timer.invalidate;
      app.terminate(null);
      return;
    }
    var cur = readFront();          // clicked this platform again while open?
    if (cur !== '' && cur !== lastFront) {
      lastFront = cur;
      win.makeKeyAndOrderFront(null);
      app.activateIgnoringOtherApps(true);
    }
  });

  app.run;   // real event loop: interactive input + healthy WebKit content process
}
