const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const server = http.createServer((req, res) => {
  const file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
  if (!file.startsWith(root + path.sep)) { res.writeHead(403).end(); return; }
  fs.readFile(file, (error, data) => {
    if (error) { res.writeHead(404).end(); return; }
    res.setHeader('Content-Type', { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }[path.extname(file)] || 'application/octet-stream');
    res.end(data);
  });
});

async function nextFrames(page, count = 3) {
  await page.evaluate(count => new Promise(resolve => {
    function frame() { if (--count <= 0) resolve(); else requestAnimationFrame(frame); }
    requestAnimationFrame(frame);
  }), count);
}
async function assertMoving(page, selector, message) {
  const previous = await page.locator(selector).innerHTML();
  await page.waitForFunction(({ selector, previous }) => document.querySelector(selector).innerHTML !== previous,
    { selector, previous }, { timeout: 3000 }).catch(error => { throw new Error(message, { cause: error }); });
}
async function assertFrozen(page, selector, message) {
  // IntersectionObserver and matchMedia notifications are asynchronous.
  await nextFrames(page);
  const previous = await page.locator(selector).innerHTML();
  await nextFrames(page, 8);
  assert.ok(await page.locator(selector).innerHTML() === previous, message);
}

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(base + '/src/vendor/aora-bot/avatar.html');
    await page.waitForFunction(() => window.agentAvatar);
    await assertFrozen(page, '#avatar', 'offline page must wait for native foreground state');
    await page.evaluate(() => agentAvatar.setActive(true));

    // Sample actual animation frames for all upstream definitions, not just the
    // synchronous/static setEmotion path. No network or model service is used.
    assert.equal(await page.evaluate(async () => {
      const host = document.createElement('div');
      document.body.appendChild(host);
      const engine = EmotionBall.create(host, { autostart: true });
      const errors = [];
      engine.on('error', value => errors.push(value));
      try {
        const emotions = EmotionBall.config.list();
        for (const emotion of emotions) {
          if (!engine.setEmotion(emotion.id)) throw new Error(emotion.id);
          for (let frame = 0; frame < 8; frame++) {
            await new Promise(requestAnimationFrame);
            if (!host.querySelector('svg')) throw new Error('Missing SVG: ' + emotion.id);
            if (/NaN|Infinity/.test(host.innerHTML)) throw new Error('Invalid SVG: ' + emotion.id);
          }
        }
        if (errors.length) throw new Error(JSON.stringify(errors));
        return emotions.length;
      } finally { engine.destroy(); host.remove(); }
    }), 32);

    const mappings = { idle: '02', offline: '06', queued: '35', sending: '31', running: '30', thinking: '30', executing: '32', searching: '40', replying: '39', awaiting_approval: '35', awaiting_input: '35', paused: '06', pause_requested: '35', succeeded: '33', failed: '34', interrupted: '34', canceled: '41', cancel_requested: '41', unexpected: '02', constructor: '02', toString: '02' };
    for (const [status, expected] of [...Object.entries(mappings), ['__proto__', '02'], [null, '02'], [42, '02'], [{}, '02']]) {
      await page.evaluate(status => agentAvatar.update(status), status);
      assert.equal(await page.locator('#avatar').getAttribute('data-emotion'), expected, String(status));
      assert.match(await page.locator('#avatar').getAttribute('aria-label'), /^Agent · /);
    }
    await page.evaluate(() => agentAvatar.update('running'));
    await assertMoving(page, '#avatar', 'foreground renderer must animate');
    await page.evaluate(() => agentAvatar.setActive(false));
    await assertFrozen(page, '#avatar', 'explicit pause must stop frames');
    // Repeated pause notifications must not redraw a different static pose.
    const paused = await page.locator('#avatar').innerHTML();
    await page.evaluate(() => { agentAvatar.setActive(false); agentAvatar.update('running'); });
    assert.equal(await page.locator('#avatar').innerHTML(), paused);
    await page.evaluate(() => agentAvatar.setActive(true));
    await assertMoving(page, '#avatar', 'resume must restart frames');

    await page.evaluate(() => { document.getElementById('avatar').hidden = true; });
    await assertFrozen(page, '#avatar', 'hidden DOM host must stop frames');
    await page.evaluate(() => { document.getElementById('avatar').hidden = false; });
    await assertMoving(page, '#avatar', 'revealed DOM host must resume');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await assertFrozen(page, '#avatar', 'reduced motion change must stop frames');
    await page.evaluate(() => agentAvatar.update('succeeded'));
    assert.equal(await page.locator('#avatar').getAttribute('data-emotion'), '33');
    await assertFrozen(page, '#avatar', 'new status must remain static with reduced motion');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await assertMoving(page, '#avatar', 'disabling reduced motion must resume');

    // Exercise browser lifecycle events, including retained pages. A cached
    // page must keep the renderer and preserve an explicit Android host pause.
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })));
    assert.equal(await page.locator('#avatar svg').count(), 1, 'cached page must retain SVG');
    await assertFrozen(page, '#avatar', 'cached page must pause');
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    await assertMoving(page, '#avatar', 'cached page must resume');
    await page.evaluate(() => {
      agentAvatar.setActive(false);
      window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    });
    await assertFrozen(page, '#avatar', 'cache restore must preserve host pause');

    // Observe upstream change events without adding test-only production APIs.
    assert.deepEqual(await page.evaluate(async () => {
      const host = document.createElement('div');
      host.style.cssText = 'position:fixed;left:0;top:0;width:100px;height:100px';
      document.body.appendChild(host);
      const create = EmotionBall.create;
      let changes = 0;
      EmotionBall.create = (...args) => {
        const engine = create(...args);
        engine.on('change', () => changes++);
        return engine;
      };
      let avatar;
      try { avatar = AgentEmotion.mount(host); }
      finally { EmotionBall.create = create; }
      try {
        const sameInstance = AgentEmotion.mount(host) === avatar;
        avatar.update('running');
        for (let i = 0; i < 20; i++) {
          avatar.update(i % 2 ? 'thinking' : 'running');
          await new Promise(requestAnimationFrame);
        }
        return { sameInstance, changes };
      } finally { avatar.destroy(); avatar.destroy(); host.remove(); }
    }), { sameInstance: true, changes: 1 }, 'equivalent status updates must not replay animation');
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false })));
    assert.equal(await page.locator('#avatar svg').count(), 0, 'discarded page must destroy SVG');
    await page.evaluate(() => { agentAvatar.update('failed'); agentAvatar.setActive(true); agentAvatar.destroy(); });
    assert.equal(await page.locator('#avatar svg').count(), 0, 'destroy must be idempotent');

    // Android WebView previously exposed only MediaQueryList.addListener.
    const legacy = await browser.newPage();
    legacy.on('pageerror', error => errors.push(error.message));
    await legacy.addInitScript(() => {
      const matchMedia = window.matchMedia.bind(window);
      window.matchMedia = query => {
        const media = matchMedia(query);
        return { get matches() { return media.matches; }, addListener: listener => media.addListener(listener), removeListener: listener => media.removeListener(listener) };
      };
    });
    await legacy.goto(base + '/src/vendor/aora-bot/avatar.html');
    await legacy.waitForFunction(() => window.agentAvatar);
    await legacy.evaluate(() => agentAvatar.setActive(true));
    await assertMoving(legacy, '#avatar', 'legacy MediaQueryList must animate');
    await legacy.emulateMedia({ reducedMotion: 'reduce' });
    await assertFrozen(legacy, '#avatar', 'legacy MediaQueryList must honor reduced motion');
    await legacy.evaluate(() => agentAvatar.destroy());
    await legacy.close();

    // Existing desktop test bridge and public debug hooks drive both real UIs.
    await page.addInitScript(() => {
      window.agentDesktop = {
        getDefaultWorkspace: () => Promise.resolve(null), getRepoRoot: () => Promise.resolve('/'),
        readTree: () => Promise.resolve({ entries: [] }), listFiles: () => Promise.resolve([]),
        readFile: () => Promise.resolve({ content: '' }), writeFile: () => Promise.resolve(),
        exists: () => Promise.resolve(false), stat: () => Promise.resolve({}),
        basename: value => Promise.resolve(value?.split(/[/\\]/).pop() || value),
        dirname: value => Promise.resolve(value?.split(/[/\\]/).slice(0, -1).join('/') || '/'),
        joinPath: (...parts) => Promise.resolve(parts.join('/')), relative: (from, to) => Promise.resolve(to), normalize: value => Promise.resolve(value),
        agentStatus: () => Promise.resolve({ running: false, managed: false, port: 8000, phoneUrl: null }),
        onAgentServerExit: () => () => {}, onMenu: () => () => {},
      };
    });
    await page.goto(base + '/src/index.html?__agent_windows_fixture=1');
    await page.waitForFunction(() => window.AiPanel?.debug && window.EditorApp?.openDiff);
    await page.waitForSelector('#aiAgentEmotion svg');
    assert.equal(await page.locator('#aiAgentEmotion > svg').evaluate(el => el.getBoundingClientRect().width), 30);
    for (const [status, id] of [['running', '30'], ['awaiting_approval', '35'], ['paused', '06'], ['succeeded', '33'], ['failed', '34']]) {
      await page.evaluate(status => AiPanel.debug.setState({ connected: true, currentJobId: 'emotion-test-job', jobStatus: status, running: status === 'running', cancelRequested: false, pauseRequested: false }), status);
      assert.equal(await page.locator('#aiAgentEmotion').getAttribute('data-emotion'), id, 'AI panel ' + status);
    }
    await page.evaluate(() => AiPanel.debug.setState({ connected: true, jobStatus: 'running', running: true }));
    await assertMoving(page, '#aiAgentEmotion', 'AI panel avatar must animate');
    await page.evaluate(() => AiPanel.debug.setState({ pauseRequested: true }));
    assert.equal(await page.locator('#aiAgentEmotion').getAttribute('data-emotion'), '35');
    await page.evaluate(() => AiPanel.debug.setState({ pauseRequested: false, cancelRequested: true }));
    assert.equal(await page.locator('#aiAgentEmotion').getAttribute('data-emotion'), '41');
    await page.evaluate(() => AiPanel.debug.setState({ connected: false, cancelRequested: false }));
    assert.equal(await page.locator('#aiAgentEmotion').getAttribute('data-emotion'), '06');

    await page.locator('[data-mode="agent-windows"]').click();
    await page.waitForSelector('#cxAgentEmotion svg');
    await assertFrozen(page, '#aiAgentEmotion', 'hidden AI panel must stop frames');
    assert.equal(await page.locator('#cxAgentEmotion > svg').evaluate(el => el.getBoundingClientRect().width), 52);
    const fixture = await page.evaluate(() => {
      const state = CodexiaAgentView._internal.getState();
      return { projects: state.projects, jobs: state.jobs };
    });
    for (const [status, id] of [['running', '30'], ['awaiting_approval', '35'], ['succeeded', '33'], ['failed', '34']]) {
      await page.evaluate(({ fixture, status }) => {
        CodexiaAgentView._internal.setDebugData({ ...fixture, jobs: fixture.jobs.map(job => ({ ...job, status, display_status: status })) });
      }, { fixture, status });
      assert.equal(await page.locator('#cxAgentEmotion').getAttribute('data-emotion'), id, 'workbench ' + status);
    }
    await assertMoving(page, '#cxAgentEmotion', 'visible workbench avatar must animate');
    await page.locator('#cxFocusPanel').click();
    assert.equal(await page.locator('#cxAgentEmotion').evaluate(el => getComputedStyle(el).visibility), 'hidden');
    await assertFrozen(page, '#cxAgentEmotion', 'focused right panel must pause the hidden main header');
    await page.locator('#cxSidebarDownload').click();
    await page.locator('[data-mode="agent-windows"]').click();
    await assertFrozen(page, '#cxAgentEmotion', 'reopening a focused workbench must keep its header paused');
    await page.locator('#cxFocusPanel').click();
    await assertMoving(page, '#cxAgentEmotion', 'leaving panel focus must resume the main header');
    await page.locator('#cxFocusPanel').click();
    await assertFrozen(page, '#cxAgentEmotion', 'reentering panel focus must pause again');
    await page.locator('#cxCollapsePanel').click();
    await assertMoving(page, '#cxAgentEmotion', 'collapsing a focused panel reveals and resumes the main header');
    await page.locator('#cxCollapsePanel').click();
    await page.locator('#cxSidebarDownload').click();
    await assertFrozen(page, '#cxAgentEmotion', 'hidden workbench avatar must stop frames');
    await assertMoving(page, '#aiAgentEmotion', 'restored AI panel must resume');
    await page.locator('[data-mode="agent-windows"]').click();
    await assertMoving(page, '#cxAgentEmotion', 'restored workbench must resume');
    const artifactDir = path.resolve(root, '../.artifacts/aora');
    fs.mkdirSync(artifactDir, { recursive: true });
    await page.screenshot({ path: path.join(artifactDir, 'desktop.png') });
    await page.setViewportSize({ width: 700, height: 800 });
    await page.screenshot({ path: path.join(artifactDir, 'desktop-narrow.png') });
    assert.deepEqual(errors, []);
    console.log('agent-emotion.test: OK (32 animated expressions, safe unknown states, both desktop entries, pause/resume, visibility, reduced motion, cache restore, cleanup)');
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
