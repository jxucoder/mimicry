(() => {
  'use strict';
  let samples, pendingSession;
  const dialog = document.getElementById('sample-dialog');
  async function api(path, data) {
    const session = await sessionData();
    const response = await fetch(path, data === undefined ? {} : {method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf,
    }, body: JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error); error.status = response.status; throw error; }
    return result;
  }
  function sessionData() {
    pendingSession ||= fetch('/api/session').then(response => response.json()).catch(error => { pendingSession = null; throw error; });
    return pendingSession;
  }
  async function settings() {
    samples ??= (await sessionData()).examples.references;
    document.getElementById('sample-text').value = samples;
    document.getElementById('sample-status').textContent = '';
    dialog.showModal();
  }
  document.getElementById('voice-settings').addEventListener('click', settings);
  document.getElementById('theme-toggle').addEventListener('click', event => {
    const dark = document.documentElement.dataset.theme !== 'dark';
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    event.currentTarget.setAttribute('aria-pressed', String(dark));
    event.currentTarget.textContent = dark ? 'Light card preview' : 'Dark card preview';
  });
  document.getElementById('save-voice').addEventListener('click', () => {
    const value = document.getElementById('sample-text').value;
    if (Array.from(value.trim()).length < 80 || Array.from(value).length > 12000) {
      document.getElementById('sample-status').textContent = 'Use 80–12,000 characters of writing samples.'; return;
    }
    samples = value; dialog.close();
  });
  window.MimicryInline.mount({
    selector: '[contenteditable="true"][role="textbox"]', allowed: () => true,
    styleURL: '/inline-assets/composer.css',
    themeFor: () => document.documentElement.dataset.theme || 'light',
    transport: {
      async bootstrap() {
        pendingSession = null;
        const session = await sessionData(); samples ??= session.examples.references;
        return {voiceReady: Array.from(samples.trim()).length >= 80, sampleCharacters: Array.from(samples.trim()).length, modelsReady: session.models_ready};
      },
      start: data => api('/api/rewrite', {...data, references: data.writing_mode === 'refine' ? '' : samples, output_format: 'text'}),
      poll: id => api('/api/jobs/' + encodeURIComponent(id)), settings,
    },
  });
})();
