(() => {
  'use strict';
  if (window.__mimicryComposerInstalled) return;
  if (!window.MimicryInlineCore || !window.MimicryInline?.mount || typeof window.MimicryInlineCSS !== 'string') {
    throw new Error('Mimicry components did not load. Reload the extension, then reload X.');
  }
  const allowed = () => ['x.com', 'twitter.com'].includes(location.hostname) &&
    !/^\/(messages|settings|i\/(chat|flow|account|oauth2))(\/|$)/.test(location.pathname);
  async function request(type, data = {}) {
    let result;
    try { result = await chrome.runtime.sendMessage({type, ...data}); }
    catch { throw new Error('Reload X to reconnect Mimicry.'); }
    if (!result || result.error) {
      const error = new Error(result?.error || 'Mimicry could not complete this request.');
      error.status = result?.status; throw error;
    }
    return result;
  }
  window.MimicryInline.mount({
    selector: '[data-testid^="tweetTextarea_"][role="textbox"][contenteditable="true"]',
    allowed, css: window.MimicryInlineCSS,
    themeFor() {
      for (const el of [document.body, document.documentElement]) {
        const rgba = getComputedStyle(el).backgroundColor.match(/[\d.]+/g)?.map(Number);
        if (rgba?.length >= 3 && (rgba.length < 4 || rgba[3] > 0.1)) {
          return rgba[0] * 0.2126 + rgba[1] * 0.7152 + rgba[2] * 0.0722 < 128 ? 'dark' : 'light';
        }
      }
      return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    },
    transport: {
      bootstrap: () => request('mimicry:inline-bootstrap'),
      start: data => request('mimicry:inline-rewrite', data),
      poll: id => request('mimicry:inline-job', {id}),
      settings: () => request('mimicry:open-settings'),
    },
    contextFor(editor) {
      // A reply dialog supplies explicit context. Never guess from the home feed.
      const article = editor.closest('[role="dialog"]')?.querySelector('article[data-testid="tweet"]');
      const text = article?.querySelector('[data-testid="tweetText"]')?.innerText?.trim();
      if (!text) return '';
      const href = article.querySelector('time')?.closest('a')?.getAttribute('href');
      const author = href?.match(/^\/([A-Za-z0-9_]{1,15})\/status\/\d+/)?.[1];
      const context = (author ? `Replying to @${author}:\n` : 'Replying to this post:\n') + text;
      return Array.from(context).length <= 12000 ? context : '';
    },
  });
  window.__mimicryComposerInstalled = true;
})();
