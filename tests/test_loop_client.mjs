import test from 'node:test';
import assert from 'node:assert/strict';
import {createLoopClient, suggestionFor} from '../integrations/chrome/loop-client.mjs';

function fixture() {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({url, ...options});
    const value = url.endsWith('/api/session') ? {csrf: 'test-csrf'} :
      {contract_version: 1, id: 'job', revision: 2, status: 'complete'};
    return {ok: true, json: async () => value};
  };
  return {client: createLoopClient({fetchImpl}), calls};
}

test('connect uses local session then versioned API with CSRF and no provider keys', async () => {
  const {client, calls} = fixture();
  await assert.rejects(client.profile(), /connect/);
  await client.connect();
  const input = {request_id: 'stable-id', source_text: 'My draft', writing_mode: 'refine'};
  await client.startRewrite(input);
  await client.startRewrite(input);
  assert.equal(calls[1].headers['x-csrf-token'], 'test-csrf');
  assert.equal(calls[2].body, calls[3].body);
  assert.equal(calls[2].credentials, 'include');
  assert.equal(calls[2].headers.Authorization, undefined);
  assert.throws(() => createLoopClient({baseUrl: 'https://remote.example'}), /local/);
});

test('job polling and cancellation use separate operations', async () => {
  const {client, calls} = fixture();
  await client.connect();
  const events = [];
  const job = await client.wait('job', {onUpdate: e => events.push(e)});
  assert.equal(job.status, 'complete');
  assert.equal(events.length, 1);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(client.wait('job', {signal: controller.signal}));
  assert.equal(calls.filter(c => c.url.endsWith('/cancel')).length, 0);
  await client.cancel('job');
  assert.equal(calls.at(-1).method, 'POST');
  assert.match(calls.at(-1).url, /\/cancel$/);
});

test('only eligible unchanged-composer results can be applied', () => {
  const run = {status: 'complete', source_text: 'original', selected: 'draft',
    review_version: 'repair', review_required: false, selected_eligible: true,
    versions: {draft: {text: 'ready'}, repair: {text: 'tentative'}}};
  assert.equal(suggestionFor(run, 'original').canApply, true);
  assert.equal(suggestionFor(run, 'user changed this').canApply, false);
  const pending = suggestionFor({...run, review_required: true}, 'original');
  assert.equal(pending.text, 'tentative'); assert.equal(pending.canApply, false);
  assert.equal(suggestionFor({...run, status: 'partial'}, 'original').canApply, false);
});

test('old contracts fail rather than being rendered as new loop results', async () => {
  const client = createLoopClient({fetchImpl: async url => ({ok: true,
    json: async () => url.endsWith('/api/session') ? {csrf: 'token'} : {contract_version: 0}})});
  await assert.rejects(client.connect(), /Unsupported/);
});

test('Chrome client preserves full posts by default and keeps profile format consistent', async () => {
  const {client, calls} = fixture();
  await client.connect();
  await client.startRewrite({request_id: 'long', source_text: 'full argument '.repeat(70)});
  assert.equal(JSON.parse(calls.at(-1).body).output_format, 'text');
  assert.match(calls[1].url, /profile\?output_format=text$/);
});
