'use strict';
const LOCAL_ORIGIN = 'http://127.0.0.1:2719';
function isXPage(url) {
  try {
    const page = new URL(url);
    return ['https://x.com', 'https://twitter.com'].includes(page.origin) &&
      !/^\/(messages|settings|i\/(chat|flow|account|oauth2))(\/|$)/.test(page.pathname);
  } catch { return false; }
}
async function deliver(tab, capture) {
  if (!tab?.id || !isXPage(tab.url) || typeof capture?.text !== 'string') throw new Error('Choose a post on X first.');
  if (!capture.text.trim() || Array.from(capture.text).length > 12000) throw new Error('Choose between 1 and 12,000 characters.');
  const item = {text: capture.text.trim(), source: tab.url, capturedAt: Date.now()};
  const opened = chrome.sidePanel.open({tabId: tab.id});
  await chrome.storage.session.set({lastCapture: item});
  await opened;
  await chrome.runtime.sendMessage({type: 'mimicry:context', capture: item}).catch(() => {});
}
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => chrome.contextMenus.create({
    id: 'mimicry-selection', title: 'Write about this with Mimicry', contexts: ['selection'],
    documentUrlPatterns: ['https://x.com/*', 'https://twitter.com/*'],
  }));
});
chrome.action.onClicked.addListener(tab => {
  if (isXPage(tab.url)) chrome.scripting.executeScript({
    target: {tabId: tab.id}, files: ['content.js'],
  }).catch(() => chrome.sidePanel.open({tabId: tab.id}).catch(() => {}));
  else chrome.sidePanel.open({tabId: tab.id}).catch(() => {});
});
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'mimicry-selection' && isXPage(tab?.url)) {
    deliver(tab, {text: info.selectionText}).catch(() => {});
  }
});
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.type?.startsWith('mimicry:inline-') && sender.tab && sender.frameId === 0 && isXPage(sender.tab.url)) {
    inlineRequest(message, sender).then(sendResponse).catch(error => sendResponse({error: error.message, status: error.status}));
    return true;
  }
  if (message.type === 'mimicry:open-settings' && sender.tab && sender.frameId === 0 && isXPage(sender.tab.url)) {
    const opened = chrome.sidePanel.open({tabId: sender.tab.id});
    chrome.storage.session.set({openVoice: true}).then(() => opened).then(() =>
      chrome.runtime.sendMessage({type: 'mimicry:show-voice'}).catch(() => {})
    ).then(() => sendResponse({ok: true})).catch(() => sendResponse({error: 'Open My voice from the Mimicry side panel.'}));
    return true;
  }
  if (message.type === 'mimicry:choose-post' && sender.tab && isXPage(sender.tab.url)) {
    deliver(sender.tab, message.capture).then(() => sendResponse({ok: true})).catch(() => sendResponse({ok: false}));
    return true;
  }
  if (message.type === 'mimicry:use-selection' && !sender.tab) {
    (async () => {
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!isXPage(tab?.url)) return {error: 'Open an X timeline or post first.'};
      const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => window.getSelection()?.toString() || ''});
      if (!result?.result?.trim()) return {error: 'Highlight text in an X post first. To edit your own draft, click its blue Mimicry button.'};
      if (Array.from(result.result).length > 12000) return {error: 'Select at most 12,000 characters.'};
      await deliver(tab, {text: result.result});
      return {ok: true};
    })().then(sendResponse).catch(() => sendResponse({error: 'Click the Mimicry toolbar icon on X to enable access to this page.'}));
    return true;
  }
});

// Content scripts get a fixed-purpose API, never credentials or arbitrary URLs.
async function localAPI(path, data, csrf) {
  let response;
  try {
    response = await fetch(LOCAL_ORIGIN + path, {
      credentials: 'include', signal: AbortSignal.timeout(12000),
      ...(data === undefined ? {} : {method: 'POST', headers: {
        'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
      }, body: JSON.stringify(data)}),
    });
  } catch { throw new Error('Mimicry is offline. Start the local app, then try again.'); }
  let result;
  try { result = await response.json(); }
  catch { throw new Error('The local app returned an unreadable response. Try again.'); }
  if (!response.ok) { const error = new Error(result.error || 'This request could not be completed.'); error.status = response.status; throw error; }
  return result;
}
async function voiceState() {
  const session = await localAPI('/api/session');
  const saved = (await chrome.storage.session.get('writingDraft')).writingDraft;
  const references = typeof saved?.references === 'string' && (saved.references || !session.voice_profile) ? saved.references : (session.workspace?.references || '');
  return {session, references};
}
async function inlineRequest(message, sender) {
  if (message.type === 'mimicry:inline-bootstrap') {
    const {session, references} = await voiceState();
    const sampleCharacters = Array.from(references.trim()).length;
    return {voiceReady: sampleCharacters >= 80 && sampleCharacters <= 12000,
      modelsReady: session.models_ready, sampleCharacters, username: session.user?.username || session.voice_profile?.username};
  }
  if (message.type === 'mimicry:inline-rewrite') {
    const source = message.source_text, context = message.feed_context || '';
    const mode = message.writing_mode || 'refine_personalize';
    if (!['refine_personalize', 'personalize', 'refine'].includes(mode)) throw new Error('Choose one of the three writing modes.');
    if (typeof source !== 'string' || Array.from(source.trim()).length < 10 || Array.from(source).length > 12000 ||
        typeof context !== 'string' || Array.from(context).length > 12000) throw new Error('Use 10–12,000 characters for your thought.');
    const {session, references} = await voiceState();
    if (mode !== 'refine' && Array.from(references.trim()).length < 80) throw new Error('Add writing samples in My voice first.');
    const job = await localAPI('/api/rewrite', {
      source_text: source, references: mode === 'refine' ? '' : references,
      // Inline editing preserves the full post; it is not a request for a 280-character summary.
      feed_context: context, output_format: 'text', writing_mode: mode,
    }, session.csrf);
    await chrome.storage.session.set({inlineJob: {id: job.id, tabId: sender.tab.id,
      documentId: sender.documentId, frameId: sender.frameId, expiresAt: Date.now() + 4 * 3600000}});
    return {id: job.id};
  }
  if (message.type === 'mimicry:inline-job') {
    const owner = (await chrome.storage.session.get('inlineJob')).inlineJob;
    if (!owner || owner.id !== message.id || owner.tabId !== sender.tab.id ||
        owner.documentId !== sender.documentId || owner.frameId !== sender.frameId || owner.expiresAt < Date.now()) {
      const error = new Error('This suggestion belongs to another draft or has expired. Check this draft again.');
      error.status = 404;
      throw error;
    }
    const job = await localAPI('/api/jobs/' + encodeURIComponent(message.id));
    const result = job.result;
    return {id: job.id, status: job.status, progress: job.progress, result: result ? {
      source_text: result.source_text, selected: result.selected, error: result.error, writing_mode: result.writing_mode,
      steps: result.steps,
      versions: Object.fromEntries(Object.entries(result.versions).map(([id, version]) => [id, {
        text: version.text, tweet_check: version.tweet_check,
        judgment: version.judgment ? {style_mean: version.judgment.style_mean, economy: version.judgment.economy,
          content_check_passed: version.judgment.content_check_passed} : null,
      }])),
    } : null};
  }
  throw new Error('Unknown inline action.');
}
chrome.tabs.onUpdated.addListener((_tabId, change, tab) => {
  if (change.status === 'complete' && tab.url?.startsWith(LOCAL_ORIGIN + '/')) {
    chrome.runtime.sendMessage({type: 'mimicry:session'}).catch(() => {});
  }
});
