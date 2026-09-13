"""Exercise real loop HTTP handlers with live providers in an isolated fictional profile."""

import argparse
import json
import time
from datetime import UTC, datetime

from starlette.testclient import TestClient

from mimicry.engine import ROOT, settings
from mimicry.extension_identity import EXTENSION_ORIGIN
from mimicry.server import create_app


def wait(client, id_):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.get('/api/loop/jobs/' + id_)
        response.raise_for_status()
        job = response.json()
        if job['status'] != 'running':
            return job
        time.sleep(0.2)
    raise RuntimeError('HTTP smoke job exceeded its deadline.')


def smoke():
    root = ROOT / 'runs' / 'loop-http' / datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    root.mkdir(parents=True)
    config = settings()
    with TestClient(create_app(config, output_root=root),
                    base_url='http://127.0.0.1:2719') as client:
        csrf = client.get('/api/session').json()['csrf']
        client.headers.update({'Origin': EXTENSION_ORIGIN, 'x-csrf-token': csrf})
        profile = client.get('/api/loop/profile')
        profile.raise_for_status()
        payload = {'request_id': 'fictional-http-write', 'writing_mode': 'refine',
                   'source_text': 'The parser accepts Unicode now. '
                                  'I plan to release it tomorrow. '
                                  'It has only been tested with the example files.',
                   'context': {'language': 'en', 'purpose': 'build_update'}}
        print('Calling the HTTP rewrite endpoint with real Luna and TypeSafe.', flush=True)
        started = client.post('/api/loop/rewrite', json=payload)
        started.raise_for_status()
        job = wait(client, started.json()['id'])
        duplicate = client.post('/api/loop/rewrite', json=payload)
        assert duplicate.json()['id'] == job['id']
        run = job['result']
        # Refine mode records edits without treating fictional text as learned style.
        feedback = client.post('/api/loop/feedback', json={
            'request_id': 'fictional-http-edit', 'run_id': run['run_id'],
            'against_version': run['review_version'], 'intent_version': run['intent_version'],
            'edited_text': payload['source_text']})
        feedback.raise_for_status()
        edited = wait(client, feedback.json()['id'])
        report = {'note': 'Real HTTP handlers and live providers; in-process test client, '
                          'not an installed Chrome test. Fictional isolated profile.',
                  'rewrite': job, 'feedback': edited,
                  'retry_returned_same_job': True,
                  'profile_contract_version': profile.json()['contract_version']}
        (root / 'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({'output': str(root), 'rewrite_status': job['status'],
            'review_required': run['review_required'],
            'text': run['versions'][run['review_version']]['text'],
            'feedback_status': edited['status'],
            'attribution_status': edited['result']['attribution_status'],
            'retry_returned_same_job': True}, indent=2), flush=True)
        return job['status'] == 'complete' and edited['status'] == 'complete'



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.parse_args()
    if not smoke():
        raise SystemExit(1)
