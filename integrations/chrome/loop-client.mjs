/** Import from the extension service worker; provider credentials stay on the server. */
export function createLoopClient({baseUrl = 'http://127.0.0.1:2719', fetchImpl = fetch, outputFormat = 'text'} = {}) {
  if (!['text', 'tweet'].includes(outputFormat)) throw new Error('Choose text or tweet output.');
  const url = new URL(baseUrl);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) {
    throw new Error('The loop client requires the local Mimicry server.');
  }
  const base = url.origin;
  let csrf;

  async function request(path, body, signal, bootstrap = false) {
    if (!bootstrap && !csrf) throw new Error('Call connect() before using the loop.');
    const response = await fetchImpl(base + path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'include', signal,
      headers: {'Content-Type': 'application/json', ...(csrf ? {'x-csrf-token': csrf} : {})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    const value = await response.json();
    if (!response.ok) {
      const error = new Error(value.error || 'The loop request failed.');
      error.status = response.status;
      throw error;
    }
    if (!bootstrap && value.contract_version !== 1) throw new Error('Unsupported loop API version.');
    return value;
  }

  const jobPath = id => '/api/loop/jobs/' + encodeURIComponent(id);
  return {
    async connect(signal) {
      const session = await request('/api/session', undefined, signal, true);
      csrf = session.csrf;
      return request('/api/loop/profile?output_format=' + outputFormat, undefined, signal);
    },
    profile: signal => request('/api/loop/profile?output_format=' + outputFormat, undefined, signal),
    // request_id is supplied by the caller and reused after a lost response; never auto-generate on retry.
    startRewrite: (input, signal) => request('/api/loop/rewrite', {output_format: outputFormat, ...input}, signal),
    startFeedback: (input, signal) => request('/api/loop/feedback', input, signal),
    getJob: (id, signal) => request(jobPath(id), undefined, signal),
    cancel: (id, signal) => request(jobPath(id) + '/cancel', {}, signal),
    async wait(id, {onUpdate = () => {}, signal, intervalMs = 500, timeoutMs = 120000} = {}) {
      if (!Number.isFinite(intervalMs) || intervalMs < 100 ||
          !Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('Invalid polling limits.');
      const deadline = Date.now() + timeoutMs;
      let revision = -1;
      for (;;) {
        if (signal?.aborted) throw signal.reason || new Error('Polling stopped.');
        const job = await request(jobPath(id), undefined, signal);
        if (job.revision !== revision) { onUpdate(job); revision = job.revision; }
        if (job.status !== 'running') return job;
        if (Date.now() >= deadline) throw new Error('Polling timed out; the server job may still run.');
        await new Promise((resolve, reject) => {
          const finish = () => { signal?.removeEventListener('abort', abort); resolve(); };
          const timer = setTimeout(finish, Math.min(intervalMs, deadline - Date.now()));
          const abort = () => {
            clearTimeout(timer); signal?.removeEventListener('abort', abort);
            reject(signal.reason || new Error('Polling stopped.'));
          };
          signal?.addEventListener('abort', abort, {once: true});
          if (signal?.aborted) abort();
        });
      }
    },
  };
}

/** A result must never overwrite a composer that changed while the job was running. */
export function suggestionFor(run, currentComposerText) {
  const stale = run.source_text !== currentComposerText;
  const version = run.review_required ? run.review_version : run.selected;
  const candidate = run.versions?.[version];
  return {
    version, text: candidate?.text || '', stale, reviewRequired: Boolean(run.review_required),
    canApply: !stale && run.status === 'complete' && !run.review_required &&
      run.selected_eligible === true && version !== 'original' && Boolean(candidate?.text),
  };
}
