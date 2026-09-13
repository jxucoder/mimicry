(() => {
  'use strict';
  const core = globalThis.MimicryInlineCore;
  const iconPaths = {
    mark: 'M3 16V4h3l6 7 6-7h3v12h-4V9l-5 6-5-6v7Z',
    close: 'M6 6l12 12M18 6 6 18',
  };
  function node(tag, text, className) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', iconPaths[name]);
    if (name === 'close') { path.setAttribute('fill', 'none'); path.setAttribute('stroke', 'currentColor'); path.setAttribute('stroke-width', '1.8'); }
    else path.setAttribute('fill', 'currentColor');
    svg.append(path); return svg;
  }
  function button(text, className, callback) {
    const el = node('button', text, className); el.type = 'button';
    el.addEventListener('click', event => { if (event.isTrusted) callback(event); });
    return el;
  }
  function mount({selector, allowed, transport, css, styleURL, contextFor = () => '', themeFor = () => 'light'}) {
    const records = new WeakMap(), instances = new Set();
    let active = null, scheduled = false;
    function schedule() {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => { scheduled = false; scan(); });
    }
    function attach(editor) {
      const host = node('mimicry-composer');
      const shadow = host.attachShadow({mode: 'open'});
      const style = styleURL ? node('link') : node('style', css);
      if (styleURL) { style.rel = 'stylesheet'; style.href = styleURL; }
      style.addEventListener('load', schedule);
      const launch = button('', 'launcher', () => toggle());
      launch.append(icon('mark')); launch.title = 'Mimicry · write in your voice';
      launch.setAttribute('aria-label', 'Mimicry writing suggestions');
      launch.setAttribute('aria-haspopup', 'dialog'); launch.setAttribute('aria-expanded', 'false');
      const card = node('section', undefined, 'card'); card.hidden = true;
      card.setAttribute('role', 'dialog'); card.setAttribute('aria-label', 'Mimicry writing assistant');
      const head = node('header', undefined, 'head');
      const mark = node('span', undefined, 'brand-icon'); mark.append(icon('mark'));
      const brand = node('span', 'mimicry', 'brand'); brand.append(node('span', '.'));
      const close = button('', 'icon-button', () => hide(true));
      close.setAttribute('aria-label', 'Close writing suggestions'); close.append(icon('close'));
      head.append(mark, brand, node('span', 'YOUR VOICE', 'head-label'), close);
      const body = node('div', undefined, 'body');
      const modeLabel = node('label', 'Writing mode', 'mode-label');
      const mode = node('select');
      for (const [value, text] of [['refine_personalize', 'AI refine + Personalize'], ['personalize', 'Personalize only'], ['refine', 'AI refine only']]) {
        const option = node('option', text); option.value = value; mode.append(option);
      }
      modeLabel.append(mode);
      const footer = node('footer', undefined, 'footer');
      const settings = button('My voice', 'text-button', async () => {
        try { await transport.settings(); } catch (error) { setError(error.message); }
      });
      footer.append(settings, node('span', 'You choose what stays.'));
      const marks = node('div', undefined, 'marks');
      card.append(head, modeLabel, body, footer); shadow.append(style, marks, launch, card);
      (editor.closest('dialog, [role="dialog"]') || document.body).append(host);
      const state = {editor, host, launch, card, position, dispose, hide,
        source: null, expected: null, edits: null, currentEdit: 0, history: [], context: '', includeContext: true, voice: null, loadingVoice: false,
        job: null, jobId: null, busy: false, mode: 'refine_personalize', interrupted: false, error: '', applied: null, message: '', timer: null};
      records.set(editor, state); instances.add(state);
      mode.addEventListener('change', () => {
        if (state.busy) return;
        state.mode = mode.value; state.job = null; state.source = null; state.jobId = null;
        state.expected = null; state.edits = null; state.history = [];
        state.applied = null; state.error = ''; state.message = ''; render();
      });
      function live() { return allowed() && editor.isConnected && editor.isContentEditable; }
      function setError(message) { state.error = message; render(); }
      function hide(focus = false) {
        card.hidden = true; launch.setAttribute('aria-expanded', 'false');
        if (active === state) active = null;
        if (focus && live()) editor.focus({preventScroll: true});
      }
      async function toggle() {
        if (!live()) return;
        if (!card.hidden) { hide(true); return; }
        active?.hide(); active = state; card.hidden = false; launch.setAttribute('aria-expanded', 'true');
        state.context = contextFor(editor); state.loadingVoice = true; render();
        close.focus({preventScroll: true});
        try { state.voice = await transport.bootstrap(); state.error = ''; }
        catch (error) { state.error = error.message; }
        finally { state.loadingVoice = false; if (live()) render(); }
      }
      async function run() {
        if (!live() || state.busy) return;
        const source = core.read(editor);
        if (Array.from(source.trim()).length < 10 || Array.from(source).length > 12000) {
          setError('Write 10–12,000 characters in your X draft first.'); return;
        }
        state.source = source; state.error = ''; state.message = ''; state.applied = null;
        state.expected = source; state.edits = null; state.history = [];
        state.job = null; state.jobId = null; state.busy = true; state.interrupted = false; render();
        try {
          const job = await transport.start({source_text: source, feed_context: state.includeContext ? state.context : '', writing_mode: state.mode});
          state.jobId = job.id;
          await poll();
        } catch (error) { state.busy = false; setError(error.message); }
      }
      async function poll(failures = 0) {
        clearTimeout(state.timer);
        if (!live()) return;
        try {
          const job = await transport.poll(state.jobId);
          if (!live()) return;
          if (job.result && core.normalize(job.result.source_text) !== core.normalize(state.source)) {
            state.busy = false; setError('This result does not match your draft. Check it again.'); return;
          }
          state.job = job; state.busy = job.status === 'running'; state.interrupted = false; state.error = ''; render();
          if (state.busy) state.timer = setTimeout(() => poll(), 1500);
        } catch (error) {
          if (!live()) return;
          if ([401, 403, 404].includes(error.status)) {
            state.busy = false; state.interrupted = false;
            state.job = {status: 'failed', progress: error.message, result: null};
            setError(error.message); return;
          }
          if (failures < 2) state.timer = setTimeout(() => poll(failures + 1), 2000);
          else { state.interrupted = true; setError(error.message); }
        }
      }
      function render() {
        const focusAction = shadow.activeElement?.dataset?.action;
        const disclosures = [...body.querySelectorAll('details[open]')].map(el => el.dataset.detail);
        body.replaceChildren();
        const result = state.job?.result;
        const refineOnly = state.mode === 'refine';
        mode.disabled = state.busy;
        const current = core.read(editor);
        const changed = state.expected !== null && current !== state.expected;
        const selected = result?.versions?.[result.selected];
        const hasSuggestion = selected && typeof selected.text === 'string' && !state.busy && result.selected !== 'original';
        if (hasSuggestion && state.edits === null) {
          state.edits = core.suggestions(state.source, core.normalize(selected.text)); state.currentEdit = 0;
        }
        const pending = (state.edits || []).filter(edit => edit.status === 'pending');
        if (state.edits && state.edits[state.currentEdit]?.status !== 'pending') state.currentEdit = state.edits.findIndex(edit => edit.status === 'pending');
        card.classList.toggle('reviewing', Boolean(hasSuggestion));
        launch.dataset.count = hasSuggestion ? String(pending.length) : '';
        const applied = state.applied && current === core.normalize(state.applied);
        launch.dataset.status = state.busy ? 'running' : hasSuggestion && pending.length && !changed ? 'ready' : 'idle';
        launch.setAttribute('aria-label', state.busy ? 'Mimicry is checking your draft' : changed ? 'Mimicry draft changed; check again' : hasSuggestion && pending.length ? 'Mimicry suggestion ready' : 'Mimicry writing suggestions');
        const title = node('h2', state.busy ? (refineOnly ? 'A clearer expression.' : 'A little more you.') : hasSuggestion ? (pending.length ? `${pending.length} suggestion${pending.length === 1 ? '' : 's'}` : 'You’re all set.') : refineOnly ? 'Make your point clearer.' : 'Your thought. Your voice.');
        body.append(title);
        const description = refineOnly ? 'Improve clarity, flow, and precision. Your personal writing samples are not used.' : state.mode === 'personalize' ? 'Match your rhythm and word choice directly, using your own writing samples.' : state.busy ? 'First, clearer expression. Then your rhythm, with each edit checked.' :
          hasSuggestion ? 'A suggestion based on your writing samples. You have the final say.' :
          'Keep writing here. Turn your rough thought into something clear, then make it sound like you.';
        if (!hasSuggestion) body.append(node('p', description, 'description'));
        if (state.busy) {
          const phases = node('div', undefined, 'phase-list');
          const improved = Boolean(result?.versions.improved);
          const labels = refineOnly ? ['Clarify your point', 'Check meaning'] : state.mode === 'personalize' ? ['Match your voice'] : ['Clarify your point', 'Match your voice'];
          for (const [index, label] of labels.entries()) {
            const phase = node('div', undefined, 'phase ' + (index === (improved && labels.length > 1 ? 1 : 0) ? 'active' : improved && index === 0 ? 'complete' : ''));
            phase.append(node('strong', String(index + 1)), node('span', label)); phases.append(phase);
          }
          body.append(phases);
        } else if (!state.job) {
          const count = state.voice?.sampleCharacters;
          body.append(node('p', state.loadingVoice ? 'Connecting to the writing service…' : refineOnly ? 'No writing samples needed.' : state.voice?.voiceReady ?
            `Your voice is ready · ${count.toLocaleString()} sample characters` : 'Add a few things you wrote in My voice to get started.', 'voice-status'));
        }
        if (!state.job && state.context) {
          const contextLabel = node('label', undefined, 'context-label');
          const include = node('input'); include.type = 'checkbox'; include.checked = state.includeContext;
          include.addEventListener('change', () => { state.includeContext = include.checked; });
          contextLabel.append(include, node('span', 'Include the post you are replying to'));
          body.append(contextLabel, node('p', state.context, 'context-text'));
        }
        if (hasSuggestion) {
          const edit = state.edits[state.currentEdit];
          if (edit) {
            const navigation = node('div', undefined, 'edit-navigation');
            const move = delta => {
              const ids = state.edits.flatMap((item, i) => item.status === 'pending' ? [i] : []);
              state.currentEdit = ids[(ids.indexOf(state.currentEdit) + delta + ids.length) % ids.length]; render();
            };
            navigation.append(node('span', `Suggestion ${pending.indexOf(edit) + 1} of ${pending.length}`),
              button('Previous', 'text-button', () => move(-1)), button('Next', 'text-button', () => move(1)));
            for (const control of navigation.querySelectorAll('button')) control.disabled = pending.length < 2;
            body.append(navigation);
            const change = node('div', undefined, 'local-change');
            change.append(node('p', edit.before ? edit.after ? 'Replace this wording' : 'Remove this wording' : 'Add this wording', 'change-label'));
            if (edit.before) change.append(node('del', edit.before, 'edit-before'));
            if (edit.after) change.append(node('ins', edit.after, 'edit-after'));
            body.append(change);
            const actions = node('div', undefined, 'actions');
            const accept = button('Accept', 'primary', () => {
              try {
                const next = core.acceptEdit(state.expected, state.edits, state.currentEdit);
                const previous = {text: state.expected, edits: state.edits.map(item => ({...item})), index: state.currentEdit};
                core.replacePart(editor, state.expected, edit, allowed);
                state.history.push(previous); state.expected = next.text; state.edits = next.edits;
                state.message = 'Edit accepted. Check again to evaluate your updated draft.'; state.error = ''; render();
              } catch (error) { setError(error.message); }
            });
            accept.dataset.action = 'accept'; accept.disabled = changed || !live();
            const dismiss = button('Dismiss', 'text-button', () => {
              state.edits[state.currentEdit].status = 'dismissed'; state.message = 'Suggestion dismissed. Your wording stays.'; render();
            });
            dismiss.dataset.action = 'dismiss'; dismiss.disabled = changed;
            actions.append(accept, dismiss); body.append(actions);
          } else body.append(node('p', 'Every suggestion has been reviewed. Keep writing, or check your updated draft.', 'description'));
          if (state.history.length) {
            const undo = button('Undo last edit', 'text-button', () => {
              try {
                const previous = state.history.at(-1);
                core.replace(editor, state.expected, previous.text, allowed);
                state.expected = previous.text;
                state.edits = previous.edits.map((item, i) => state.edits[i]?.status === 'dismissed' ? {...item, status: 'dismissed'} : item);
                state.currentEdit = previous.index;
                state.history.pop(); state.message = 'Last edit undone.'; state.error = ''; render();
              } catch (error) { setError(error.message); }
            });
            undo.disabled = changed; undo.dataset.action = 'undo'; body.append(undo);
          }
          const complete = node('details'); complete.dataset.detail = 'complete';
          complete.append(node('summary', 'Full rewrite & checks'), node('p', selected.text, 'earlier'));
          complete.append(node('p', 'The complete rewrite was checked. Individual edits are not re-evaluated until you check again.', 'review-note'));
          for (const step of result.steps || []) complete.append(node('p', step.reason, 'decision'));
          body.append(complete);
        }
        if (!hasSuggestion && (result?.steps?.length || result?.versions.improved)) {
          const detail = node('details'); detail.dataset.detail = 'loop';
          detail.append(node('summary', refineOnly ? 'Refinement & meaning checks' : 'AI rewrite & loop decisions'));
          if (result.versions.improved) detail.append(node('p', result.versions.improved.text, 'earlier'));
          for (const step of result.steps || []) {
            const row = node('div', undefined, 'decision');
            const name = step.version === 'improved' ? 'Clearer expression' : step.version.replace('rewrite_', 'Style pass ');
            row.append(node('strong', `${name} · ${step.accepted ? 'kept' : 'not kept'}`), node('span', step.reason)); detail.append(row);
          }
          body.append(detail);
        }
        const status = node('p', '', 'status'); status.setAttribute('role', 'status'); status.setAttribute('aria-atomic', 'true');
        if (state.error) { status.textContent = state.error; status.classList.add('error'); }
        else if (changed && !applied && state.source !== null) { status.textContent = 'Your draft changed. Check it again to use your latest words.'; status.classList.add('error'); }
        else if (state.busy) status.textContent = state.job?.progress || 'Starting your rewrite…';
        else if (state.message) status.textContent = state.message;
        else if (result?.error) { status.textContent = result.error; status.classList.add('error'); }
        else if (state.job && !hasSuggestion) status.textContent = state.job.status === 'complete' ? 'No rewrite passed all checks. Your original wording stays.' : state.job.progress;
        body.append(status);
        if (!state.busy || state.interrupted) {
          const check = button(state.interrupted ? 'Reconnect to suggestion' : state.job ? 'Check this draft again' : refineOnly ? 'Refine my draft' : state.mode === 'personalize' ? 'Personalize my draft' : 'Refine & personalize', state.job ? 'text-button' : 'primary', () => state.interrupted ? poll() : run());
          check.dataset.action = 'check';
          check.disabled = state.loadingVoice || (!state.job && ((!refineOnly && !state.voice?.voiceReady) || !state.voice?.modelsReady));
          const actions = node('div', undefined, 'actions'); actions.append(check);
          if (!state.job && !state.loadingVoice && (!state.voice || (!refineOnly && !state.voice.voiceReady) || !state.voice.modelsReady)) {
            const setup = button(state.error ? 'Retry connection' : 'Set up My voice', 'text-button', async () => {
              if (state.error) { hide(); await toggle(); } else await transport.settings().catch(error => setError(error.message));
            }); actions.append(setup);
          }
          body.append(actions);
        }
        if (!state.job) body.append(node('p', refineOnly ? 'Checking sends this draft and selected context to OpenAI and TypeSafe. Nothing is posted.' : 'Only checking sends this draft, selected context, and your writing samples to OpenAI and TypeSafe. Nothing is posted.', 'privacy'));
        for (const detail of body.querySelectorAll('details')) detail.open = disclosures.includes(detail.dataset.detail);
        if (focusAction) body.querySelector(`[data-action="${focusAction}"]`)?.focus({preventScroll: true});
        position();
      }
      function position() {
        const theme = themeFor(editor);
        if (host.dataset.theme !== theme) host.dataset.theme = theme;
        const rect = editor.getBoundingClientRect();
        const modal = [...document.querySelectorAll('[role="dialog"][aria-modal="true"], dialog[open]')].filter(el => el.getBoundingClientRect().width > 0).at(-1);
        const visible = live() && (!modal || modal.contains(editor)) && rect.width > 0 && rect.height > 0 && rect.bottom > 20 && rect.top < innerHeight - 20;
        host.hidden = !visible;
        if (!visible) return;
        let anchor = null;
        marks.replaceChildren();
        if (!state.busy && state.edits && state.expected === core.read(editor)) {
          for (const [index, edit] of state.edits.entries()) {
            if (edit.status !== 'pending') continue;
            const range = core.textRange(editor, edit.start, edit.end);
            if (!range) continue;
            const boxes = [...range.getClientRects()];
            for (const [line, box] of boxes.entries()) {
              const x = Math.max(rect.left, box.left), y = box.bottom - 3;
              const width = Math.min(rect.right, box.right) - x;
              if (y < Math.max(rect.top, 0) || y > Math.min(rect.bottom, innerHeight) || width < 0) continue;
              const marker = button('', 'edit-mark' + (index === state.currentEdit && !card.hidden ? ' selected' : ''), () => {
                active?.hide(); active = state; state.currentEdit = index;
                card.hidden = false; launch.setAttribute('aria-expanded', 'true'); render();
                body.querySelector('[data-action="accept"]')?.focus({preventScroll: true});
              });
              marker.setAttribute('aria-label', `Review suggestion ${index + 1}: ${edit.before.trim().slice(0, 70) || 'Insert text'}`);
              marker.setAttribute('aria-haspopup', 'dialog');
              if (line) { marker.tabIndex = -1; marker.setAttribute('aria-hidden', 'true'); }
              marker.addEventListener('pointerdown', event => event.preventDefault());
              marker.style.left = `${x}px`; marker.style.top = `${y}px`; marker.style.width = `${Math.max(8, width)}px`;
              marks.append(marker);
              if (index === state.currentEdit && !anchor) anchor = box;
            }
          }
        }
        const left = Math.min(innerWidth - 48, rect.right + 4);
        const top = Math.max(8, Math.min(innerHeight - 44, rect.bottom - 34));
        launch.style.left = `${left}px`; launch.style.top = `${top}px`;
        if (!card.hidden) {
          const width = card.getBoundingClientRect().width, height = card.getBoundingClientRect().height;
          card.style.left = `${Math.max(12, Math.min(innerWidth - width - 12, anchor ? anchor.left : rect.right - width + 40))}px`;
          const below = anchor ? anchor.bottom + 10 : top + 44;
          const above = (anchor ? anchor.top : top) - height - 8;
          if (anchor && below + height > innerHeight - 12 && above < 12 && rect.left > width + 26) {
            card.style.left = `${rect.left - width - 14}px`;
            card.style.top = `${Math.max(12, Math.min(anchor.top, innerHeight - height - 12))}px`;
          } else card.style.top = `${below + height <= innerHeight - 12 ? below : Math.max(12, Math.min(above, innerHeight - height - 12))}px`;
        }
      }
      function onInput() { if (!card.hidden) render(); schedule(); }
      function dispose() { clearTimeout(state.timer); editor.removeEventListener('input', onInput); host.remove(); records.delete(editor); instances.delete(state); if (active === state) active = null; }
      editor.addEventListener('input', onInput);
      shadow.addEventListener('keydown', event => { event.stopPropagation(); if (event.key === 'Escape') hide(true); });
      shadow.addEventListener('click', event => event.stopPropagation());
      position();
    }
    function scan() {
      for (const instance of instances) {
        if (!instance.editor.isConnected || !allowed()) instance.dispose(); else instance.position();
      }
      if (!allowed()) return;
      for (const editor of document.querySelectorAll(selector)) {
        if (!records.has(editor) && editor.isContentEditable) attach(editor);
      }
    }
    const observer = new MutationObserver(schedule);
    observer.observe(document.body, {childList: true, characterData: true, subtree: true,
      attributes: true, attributeFilter: ['contenteditable', 'role', 'data-testid']});
    const themeObserver = new MutationObserver(schedule);
    for (const el of [document.body, document.documentElement]) themeObserver.observe(el, {attributes: true, attributeFilter: ['style', 'class', 'data-theme']});
    document.addEventListener('pointerdown', event => { if (active && !event.composedPath().includes(active.host)) active.hide(); });
    // X creates and activates reply and quote editors after the initial page load.
    document.addEventListener('focusin', schedule, true);
    document.addEventListener('input', schedule, true);
    window.addEventListener('popstate', schedule);
    document.addEventListener('scroll', schedule, true); window.addEventListener('resize', schedule);
    scan();
    return {scan};
  }
  globalThis.MimicryInline = Object.freeze({mount});
})();
