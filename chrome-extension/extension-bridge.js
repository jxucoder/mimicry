'use strict';
let draftTimer, isReady = false, queuedCapture;
const showMessage = message => {
  const note = document.getElementById('notice'); note.textContent = message; note.hidden = !message;
};
function acceptCapture(capture) {
  if (!isReady) { queuedCapture = capture; return; }
  window.dispatchEvent(new CustomEvent('mimicry:context', {detail: capture}));
  chrome.storage.session.remove('lastCapture');
}
window.mimicryHost = {
  apiBase: 'http://127.0.0.1:2719',
  async openAuth(url) {
    const destination = new URL(url);
    if (destination.origin !== 'https://x.com' || destination.pathname !== '/i/oauth2/authorize') throw new Error('Unexpected X sign-in address.');
    await chrome.tabs.create({url});
    showMessage('Finish signing in on X. This side panel will reconnect when you return.');
  },
  saveDraft(draft) {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => chrome.storage.session.set({writingDraft: draft}), 300);
  },
  async saveNow(draft) { clearTimeout(draftTimer); await chrome.storage.session.set({writingDraft: draft}); },
  async loadDraft() { return (await chrome.storage.session.get('writingDraft')).writingDraft; },
  async clearDraft() { clearTimeout(draftTimer); await chrome.storage.session.remove(['writingDraft', 'lastCapture', 'inlineJob']); },
  async ready() {
    isReady = true;
    const capture = queuedCapture || (await chrome.storage.session.get('lastCapture')).lastCapture;
    queuedCapture = undefined;
    if (capture) acceptCapture(capture);
    if ((await chrome.storage.session.get('openVoice')).openVoice) {
      document.getElementById('voice-dialog').showModal();
      await chrome.storage.session.remove('openVoice');
    }
  },
};
chrome.runtime.onMessage.addListener(message => {
  if (message.type === 'mimicry:context') acceptCapture(message.capture);
  if (message.type === 'mimicry:session') window.dispatchEvent(new Event('mimicry:session'));
  if (message.type === 'mimicry:show-voice' && isReady) {
    document.getElementById('voice-dialog').showModal(); chrome.storage.session.remove('openVoice');
  }
});
document.addEventListener('DOMContentLoaded', () => {
  const controls = document.createElement('div'); controls.className = 'capture-controls';
  const help = document.createElement('p'); help.textContent = 'Write directly in X and click the blue Mimicry button beside your draft. This studio is here for voice setup and detailed review.';
  const select = document.createElement('button'); select.className = 'quiet'; select.textContent = 'Use selected text';
  select.addEventListener('click', async () => {
    select.disabled = true;
    try { const result = await chrome.runtime.sendMessage({type: 'mimicry:use-selection'}); if (result?.error) showMessage(result.error); }
    catch { showMessage('Click the Mimicry toolbar icon on X, then try again.'); }
    finally { select.disabled = false; }
  });
  const paste = document.createElement('button'); paste.className = 'text-button'; paste.textContent = 'Paste a post';
  paste.addEventListener('click', () => document.getElementById('context-dialog').showModal());
  const reconnect = document.createElement('button'); reconnect.className = 'text-button'; reconnect.textContent = 'Reconnect';
  reconnect.addEventListener('click', () => window.dispatchEvent(new Event('mimicry:session')));
  controls.append(help, select, paste, reconnect);
  document.querySelector('.thought-section').prepend(controls);
});
