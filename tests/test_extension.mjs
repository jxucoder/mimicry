import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {test} from 'node:test';
import vm from 'node:vm';

const root = new URL('../', import.meta.url);
const source = readFileSync(new URL('chrome-extension/background.js', root), 'utf8');

function worker() {
  const event = () => ({addListener(fn) { this.run = fn; }});
  const opened = [], stored = [], sent = [], injected = [], requests = [], memory = {};
  const chrome = {
    runtime: {id: 'test-extension', onInstalled: event(), onMessage: event(),
      sendMessage: async message => sent.push(message)},
    sidePanel: {open: async data => opened.push(data)},
    storage: {session: {
      set: async data => { stored.push(data); Object.assign(memory, data); },
      get: async key => ({[key]: memory[key]}),
    }},
    contextMenus: {onClicked: event(), removeAll: fn => fn(), create: () => {}},
    action: {onClicked: event()},
    scripting: {executeScript: async data => { injected.push(data); return [{result: 'A selected excerpt.'}]; }},
    tabs: {onUpdated: event(), query: async () => [{id: 7, url: 'https://x.com/home'}]},
  };
  const replies = {
    '/api/session': {csrf: 'mock-csrf', models_ready: true, user: {username: 'test_author'},
      workspace: {references: 'My own words. '.repeat(10)}, examples: {}},
    '/api/rewrite': {id: 'job-1'},
    '/api/jobs/job-1': {id: 'job-1', status: 'complete', progress: 'Finished', result: {
      source_text: 'My own rough opinion.', references: 'private-reference', calls: ['private-call'],
      selected: 'rewrite_1', steps: [], versions: {rewrite_1: {text: 'My clearer opinion.',
        judgment: {style_mean: 3, economy: 4, content_check_passed: true, answers: ['raw-answer']}}},
    }},
  };
  const fetch = async (url, options) => {
    requests.push({url, options});
    assert.ok(url.startsWith('http://127.0.0.1:2719/'));
    const result = replies[new URL(url).pathname];
    return {ok: Boolean(result), status: result ? 200 : 404, json: async () => result || {error: 'Not found'}};
  };
  vm.runInNewContext(source, {chrome, URL, fetch, AbortSignal});
  const message = (payload, tab, extra = {}) => new Promise(resolve => {
    const pending = chrome.runtime.onMessage.run(payload, {id: chrome.runtime.id, tab, frameId: 0, documentId: 'doc-1', ...extra}, resolve);
    if (!pending) resolve(undefined);
  });
  return {chrome, opened, stored, sent, injected, message, requests, memory, replies};
}

test('toolbar enables inline assistance without opening the studio or reading posts', async () => {
  const app = worker(), tab = {id: 7, url: 'https://x.com/home'};
  app.chrome.action.onClicked.run(tab);
  assert.equal(app.injected.length, 1);
  assert.equal(app.stored.length, 0);
  assert.equal(app.sent.length, 0);
  assert.equal(app.opened.length, 0);
  const reply = await app.message({type: 'mimicry:choose-post', capture: {text: '  Chosen post.  '}}, tab);
  assert.equal(reply.ok, true);
  assert.equal(app.stored[0].lastCapture.text, 'Chosen post.');
  assert.equal(app.sent[0].type, 'mimicry:context');
  assert.equal(app.opened[0].tabId, 7);
});

test('inline bootstrap exposes readiness, not references, CSRF, or the workspace', async () => {
  const app = worker(), tab = {id: 7, url: 'https://x.com/home'};
  const response = await app.message({type: 'mimicry:inline-bootstrap'}, tab);
  assert.equal(response.voiceReady, true);
  assert.equal(response.modelsReady, true);
  assert.equal(response.csrf, undefined);
  assert.equal(response.references, undefined);
  assert.equal(response.workspace, undefined);
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].options.method, undefined);
});

test('inline rewriting uses only supplied text and saved voice; jobs bind to the originating document', async () => {
  const app = worker(), tab = {id: 7, url: 'https://x.com/home'};
  app.memory.writingDraft = {references: 'Saved personal voice. '.repeat(5), feed_context: 'Unrelated old context'};
  const started = await app.message({type: 'mimicry:inline-rewrite', source_text: 'My own rough opinion.'}, tab);
  assert.equal(started.id, 'job-1');
  const post = app.requests.find(request => request.options.method === 'POST');
  const data = JSON.parse(post.options.body);
  assert.equal(data.references, app.memory.writingDraft.references);
  assert.equal(data.feed_context, '');
  assert.equal(data.source_text, 'My own rough opinion.');
  assert.equal(data.output_format, 'text');
  assert.equal(post.options.headers['X-CSRF-Token'], 'mock-csrf');
  for (const [otherTab, documentId, frameId] of [[8, 'doc-1', 0], [7, 'doc-2', 0], [7, 'doc-1', 1]]) {
    const previous = app.requests.length;
    const rejected = await app.message({type: 'mimicry:inline-job', id: started.id}, {...tab, id: otherTab}, {documentId, frameId});
    assert.ok(!rejected || rejected.error);
    assert.equal(app.requests.length, previous);
  }
  const result = await app.message({type: 'mimicry:inline-job', id: started.id}, tab);
  assert.equal(result.result.versions.rewrite_1.text, 'My clearer opinion.');
  assert.equal(result.result.references, undefined);
  assert.equal(result.result.calls, undefined);
  assert.equal(result.result.versions.rewrite_1.judgment.answers, undefined);
});

test('empty voice, invalid source, and private-page requests cannot start a rewrite', async () => {
  const app = worker(), tab = {id: 7, url: 'https://x.com/home'};
  app.memory.writingDraft = {references: ''};
  assert.match((await app.message({type: 'mimicry:inline-rewrite', source_text: 'My own rough opinion.'}, tab)).error, /My voice/);
  assert.match((await app.message({type: 'mimicry:inline-rewrite', source_text: 'short'}, tab)).error, /characters/);
  assert.equal(await app.message({type: 'mimicry:inline-bootstrap'}, {...tab, url: 'https://x.com/i/chat'}), undefined);
  assert.ok(app.requests.every(request => request.options.method !== 'POST'));
});

test('Refine only needs no voice samples and forwards its mode without reference text', async () => {
  const app = worker(), tab = {id: 7, url: 'https://x.com/home'};
  app.memory.writingDraft = {references: ''};
  assert.equal((await app.message({type: 'mimicry:inline-rewrite', source_text: 'My own rough opinion.', writing_mode: 'refine'}, tab)).id, 'job-1');
  const data = JSON.parse(app.requests.find(request => request.options.method === 'POST').options.body);
  assert.equal(data.references, ''); assert.equal(data.writing_mode, 'refine');
  const count = app.requests.length;
  assert.match((await app.message({type: 'mimicry:inline-rewrite', source_text: 'My own rough opinion.', writing_mode: 'other'}, tab)).error, /three writing modes/);
  assert.equal(app.requests.length, count);
});

test('My voice is opened only on explicit request from an allowed X document', async () => {
  const app = worker(), tab = {id: 7, url: 'https://x.com/home'};
  assert.equal((await app.message({type: 'mimicry:open-settings'}, tab)).ok, true);
  assert.equal(app.opened[0].tabId, 7);
  assert.equal(app.memory.openVoice, true);
});

const inlineContext = {};
vm.runInNewContext(readFileSync(new URL('src/mimicry/web/inline/core.js', root), 'utf8'), inlineContext);
const core = inlineContext.MimicryInlineCore;

test('applying and undoing edits target exactly one connected, unchanged editor', () => {
  let commands = 0;
  const editor = {innerText: 'My own rough opinion.', isConnected: true, isContentEditable: true,
    focus() {}, ownerDocument: {
      getSelection: () => ({removeAllRanges() {}, addRange() {}}),
      createRange: () => ({selectNodeContents: target => assert.equal(target, editor)}),
      execCommand(command, ui, text) { assert.equal(command, 'insertText'); assert.equal(ui, false); editor.innerText = text; commands++; return true; },
    }};
  const original = editor.innerText, suggestion = 'My clearer opinion.';
  core.replace(editor, original, suggestion, () => true);
  assert.equal(editor.innerText, suggestion);
  core.replace(editor, suggestion, original, () => true);
  assert.equal(editor.innerText, original);
  editor.innerText = 'New words typed while the suggestion was running.';
  assert.throws(() => core.replace(editor, original, suggestion, () => true), /draft changed/);
  editor.innerText = original;
  editor.isConnected = false;
  assert.throws(() => core.replace(editor, original, suggestion, () => true), /no longer/);
  editor.isConnected = true;
  assert.throws(() => core.replace(editor, original, suggestion, () => false), /no longer/);
  assert.equal(commands, 2);
});

test('diffs preserve both texts, including HTML-like text, Unicode, and long input', () => {
  for (const [before, after] of [['a bad draft', 'a better draft'], ['<script>raw</script>', '<b>plain</b>'],
    ['这就是\n自己的想法', '这就是我的想法。'], ['', 'new'], ['x '.repeat(500), 'y '.repeat(500)]]) {
    const parts = core.changes(before, after);
    assert.equal(parts.filter(part => part.kind !== 'add').map(part => part.text).join(''), before);
    assert.equal(parts.filter(part => part.kind !== 'remove').map(part => part.text).join(''), after);
  }
});

test('private X surfaces and unrelated sites reject capture and toolbar injection', async () => {
  for (const url of ['https://x.com/messages/42', 'https://x.com/i/chat',
    'https://x.com/settings/profile', 'https://x.com/i/oauth2/authorize',
    'https://example.com/', 'https://x.com.evil.example/home']) {
    const app = worker(), tab = {id: 7, url};
    app.chrome.action.onClicked.run(tab);
    assert.equal(app.injected.length, 0, url);
    await app.message({type: 'mimicry:choose-post', capture: {text: 'Private content'}}, tab);
    app.chrome.contextMenus.onClicked.run({menuItemId: 'mimicry-selection', selectionText: 'Private content'}, tab);
    assert.equal(app.stored.length, 0, url);
    assert.equal(app.sent.length, 0, url);
  }
});

test('empty or oversized captures are rejected before storage', async () => {
  const app = worker(), tab = {id: 7, url: 'https://x.com/home'};
  for (const text of ['', '  ', 'x'.repeat(12001)]) {
    assert.equal((await app.message({type: 'mimicry:choose-post', capture: {text}}, tab)).ok, false);
  }
  assert.equal(app.opened.length, 0);
  assert.equal(app.stored.length, 0);
});

test('selection action reads only the active X tab and rejects other pages', async () => {
  const app = worker();
  assert.equal((await app.message({type: 'mimicry:use-selection'})).ok, true);
  assert.equal(app.injected[0].target.tabId, 7);
  assert.equal(app.stored[0].lastCapture.text, 'A selected excerpt.');
  app.chrome.tabs.query = async () => [{id: 8, url: 'https://x.com/messages'}];
  assert.match((await app.message({type: 'mimicry:use-selection'})).error, /Open an X/);
  assert.equal(app.injected.length, 1);
});

test('bundle identity matches the backend allowlist and ships no remote scripts', () => {
  const read = path => readFileSync(new URL(path, root), 'utf8');
  const manifest = JSON.parse(read('chrome-extension/manifest.json'));
  const digest = createHash('sha256').update(Buffer.from(manifest.key, 'base64')).digest('hex');
  const id = [...digest.slice(0, 32)].map(c => String.fromCharCode(97 + parseInt(c, 16))).join('');
  assert.ok(read('src/mimicry/extension_identity.py').includes('chrome-extension://' + id));
  assert.deepEqual(manifest.host_permissions, ['http://127.0.0.1/*']);
  assert.deepEqual(manifest.content_scripts, [{
    matches: ['https://x.com/*', 'https://twitter.com/*'], js: ['content.js'],
    run_at: 'document_idle', all_frames: false, world: 'ISOLATED',
  }]);
  assert.ok(manifest.content_security_policy.extension_pages.includes("script-src 'self'"));
  for (const asset of ['app.js', 'app.css']) {
    assert.equal(read('chrome-extension/assets/' + asset), read('src/mimicry/web/' + asset));
  }
});

test('individual suggestions reconstruct Unicode and multiline revisions in any acceptance order', () => {
  for (const [before, after] of [
    ['We really like the idea.\nBut the launch is very slow.', 'We like the idea.\nBut the launch is slow.'],
    ['你好 👋. This is a completely separate sentence. End.', '你好朋友 👋. This is a completely separate sentence. Done.'],
    ['The launch is tomorrow.', 'Actually, the launch is tomorrow.'],
    ['One sentence.', ''],
  ]) {
    for (const reverse of [false, true]) {
      let text = before, edits = core.suggestions(before, after);
      const ids = edits.map((_, i) => i); if (reverse) ids.reverse();
      for (const i of ids) ({text, edits} = core.acceptEdit(text, edits, i));
      assert.equal(text, after);
      assert.ok(edits.every(edit => edit.status === 'accepted'));
    }
  }
});

test('dismissing an edit preserves its words and stale or repeated acceptance fails', () => {
  const before = 'I really like this approach. The next sentence is entirely separate. It is very good.';
  const after = 'I like this approach. The next sentence is entirely separate. It is good.';
  const edits = core.suggestions(before, after);
  assert.equal(edits.length, 2);
  edits[0].status = 'dismissed';
  const accepted = core.acceptEdit(before, edits, 1);
  assert.equal(accepted.text, 'I really like this approach. The next sentence is entirely separate. It is good.');
  assert.throws(() => core.acceptEdit(accepted.text, accepted.edits, 1), /no longer/);
  assert.throws(() => core.acceptEdit('Different text', core.suggestions(before, after), 0), /no longer/);
});

test('related changes in one sentence stay atomic', () => {
  const before = 'We wanted to share an update regarding the office coffee machine. The machine is currently broken, and repairs will take three days. The company provides instant coffee.';
  const after = 'The office coffee machine is broken and will take three days to repair. The company provides instant coffee.';
  const edits = core.suggestions(before, after);
  assert.equal(edits.length, 1);
  assert.equal(core.acceptEdit(before, edits, 0).text, after);
});

test('an explicitly imported API voice fills an empty extension draft without replacing existing samples', async () => {
  const app = worker();
  app.replies['/api/session'].voice_profile = {username: 'test_author'};
  app.memory.writingDraft = {references: ''};
  const tab = {id: 7, url: 'https://x.com/home'};
  const ready = await app.message({type: 'mimicry:inline-bootstrap'}, tab);
  assert.equal(ready.voiceReady, true);
  app.memory.writingDraft.references = 'My deliberately chosen samples. '.repeat(5);
  const own = await app.message({type: 'mimicry:inline-bootstrap'}, tab);
  assert.equal(own.sampleCharacters, app.memory.writingDraft.references.trim().length);
});

test('failed content initialization remains retryable and successful mounting happens once', () => {
  const source = readFileSync(new URL('src/mimicry/web/inline/extension-entry.js', root), 'utf8');
  const context = {window: {}, location: {hostname: 'x.com', pathname: '/home'}};
  assert.throws(() => vm.runInNewContext(source, context), /components did not load/);
  assert.equal(context.window.__mimicryComposerInstalled, undefined);
  let mounts = 0;
  context.window.MimicryInlineCore = {};
  context.window.MimicryInlineCSS = 'styles';
  context.window.MimicryInline = {mount() { throw new Error('mount failed'); }};
  assert.throws(() => vm.runInNewContext(source, context), /mount failed/);
  assert.equal(context.window.__mimicryComposerInstalled, undefined);
  context.window.MimicryInline.mount = () => mounts++;
  vm.runInNewContext(source, context);
  vm.runInNewContext(source, context);
  assert.equal(mounts, 1);
});

test('the shipped content.js boots alone without preloaded globals and does not mount twice', () => {
  let observations = 0;
  const context = {
    location: {hostname: 'x.com', pathname: '/home'},
    document: {body: {}, documentElement: {}, addEventListener() {}, querySelectorAll: () => []},
    MutationObserver: class { observe() { observations++; } },
    addEventListener() {},
  };
  context.window = context;
  const script = readFileSync(new URL('chrome-extension/content.js', root), 'utf8');
  vm.runInNewContext(script, context);
  assert.equal(typeof context.MimicryInline.mount, 'function');
  assert.equal(typeof context.MimicryInlineCore.read, 'function');
  assert.equal(typeof context.MimicryInlineCSS, 'string');
  assert.equal(context.__mimicryComposerInstalled, true);
  const initialObservations = observations;
  assert.ok(initialObservations > 0);
  vm.runInNewContext(script, context);
  assert.equal(observations, initialObservations);
  const app = worker();
  app.chrome.action.onClicked.run({id: 7, url: 'https://x.com/home'});
  assert.deepEqual(Array.from(app.injected[0].files), ['content.js']);
});

test('long inline posts preserve their full source instead of requesting a standard tweet', async () => {
  const app = worker(), tab = {id: 7, url: 'https://x.com/home'};
  const source = 'Mercor spends 3X as much on inference as salaries. '.repeat(12);
  const result = await app.message({type: 'mimicry:inline-rewrite', source_text: source}, tab);
  assert.equal(result.id, 'job-1');
  const data = JSON.parse(app.requests.find(r => r.options.method === 'POST').options.body);
  assert.equal(data.source_text, source);
  assert.equal(data.output_format, 'text');
});

test('automatic discovery rescans dynamic editors without toolbar clicks or API requests', () => {
  const events = new Map(), observers = [], frames = [];
  let scans = 0;
  const listen = (name, callback) => events.set(name, callback);
  const context = {
    location: {hostname: 'x.com', pathname: '/home'},
    document: {body: {}, documentElement: {}, addEventListener: listen,
      querySelectorAll(selector) {
        assert.equal(selector, '[data-testid^="tweetTextarea_"][role="textbox"][contenteditable="true"]');
        scans++; return [];
      }},
    MutationObserver: class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe(target, options) { this.options = options; }
    },
    addEventListener: listen,
    requestAnimationFrame: callback => frames.push(callback),
    chrome: {runtime: {sendMessage() { assert.fail('Discovery must not call the backend'); }}},
  };
  context.window = context;
  vm.runInNewContext(readFileSync(new URL('chrome-extension/content.js', root), 'utf8'), context);
  assert.equal(scans, 1, 'Initial page load scans automatically');
  for (const trigger of [() => observers[0].callback(), () => events.get('focusin')(),
    () => events.get('input')(), () => events.get('popstate')()]) {
    const before = scans;
    trigger(); frames.shift()();
    assert.equal(scans, before + 1);
  }
  assert.ok(observers[0].options.attributeFilter.includes('contenteditable'));
  context.location.pathname = '/messages';
  const before = scans;
  events.get('focusin')(); frames.shift()();
  assert.equal(scans, before, 'Private routes must not query editors');
  context.location.pathname = '/home';
  events.get('focusin')(); frames.shift()();
  assert.equal(scans, before + 1, 'SPA navigation back to X resumes discovery');
});
