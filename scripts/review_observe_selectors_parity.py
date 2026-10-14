#!/usr/bin/env python3
"""Actual resident agent_observe selector comparison, pinned TS vs Rust.

Only identity/transcript selection, ambiguity and family rejection are compared.
Full summary/preview objects and differing rejection messages remain in receipt;
passive hydration, transient progress events and preview fidelity are excluded.
"""
import argparse
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from review_agent_bounce_parity import Client
from review_draft_disconnect_parity import digest, TS_ARCHIVE_SHA256
from review_goal_fixture_lifecycle import OwnedProcesses, TAG, finish, verify_worker
from review_restore_notice_parity import texts

MARKER = 'OBSERVE_SELECTORS_PARITY='
LIMIT = 35


class ObserveClient(Client):
    def call(self, kind, timeout_seconds=LIMIT, **payload):
        self.counter += 1
        ident = str(self.counter)
        envelope = {'type': 'command', 'id': ident,
            'protocol': {'name': 'prime-agent.daemon', 'version': 7},
            'command': {'type': kind, **payload}}
        self.sent.append(envelope)
        deadline = time.monotonic() + timeout_seconds
        previous_timeout = self.sock.gettimeout()
        try:
            self.sock.settimeout(timeout_seconds)
            self.sock.sendall((json.dumps(envelope) + '\n').encode())
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.001, deadline - time.monotonic()))
                line = self.reader.readline()
                assert line, 'daemon closed connection'
                response = json.loads(line)
                self.wire.append(response)
                if response.get('type') == 'response' and response.get('id') == ident:
                    assert response.get('success'), response
                    return response.get('data')
            raise TimeoutError('command never answered: ' + kind)
        finally:
            self.sock.settimeout(previous_timeout)

    def __init__(self, path, process):
        super().__init__(path, process)
        self.sent = []


def cell(selectors):
    return """import json, agent_observe
selectors = """ + repr(selectors) + """
result = {'roster': await agent_observe.list_agents(), 'selectors': {}}
for label, target in selectors.items():
    row = result['selectors'][label] = {}
    for operation in ('get', 'recent'):
        try:
            value = (await agent_observe.get_agent(target) if operation == 'get'
                     else await agent_observe.recent_messages(target, limit=8, max_chars=800))
            row[operation] = {'value': value}
        except Exception as error:
            row[operation] = {'error': str(error)}
print('OBSERVE_SELECTORS_PARITY=' + json.dumps(result))
"""


class Provider(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.phase, self.code = None, None
        self.requests = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        phase = self.server.phase
        requests = self.server.requests.setdefault(phase, [])
        requests.append(body)
        if self.server.code is not None and len(requests) == 1:
            offered = {tool.get('function', {}).get('name') for tool in body.get('tools', [])}
            name = next((name for name in ('python_repl', 'ipython') if name in offered), None)
            if name is None:
                self.send_error(422, 'no actual REPL tool offered')
                return
            delta = {'tool_calls': [{'index': 0, 'id': 'observe_' + phase, 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps({'code': self.server.code})}}]}
            reason = 'tool_calls'
        else:
            delta, reason = {'content': 'TRANSCRIPT_' + phase.upper()}, 'stop'
        def chunk(delta, reason=None):
            return {'id': 'observe-selectors', 'object': 'chat.completion.chunk', 'created': 1,
                'model': 'observe-fixture', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
        wire = ''.join('data: ' + json.dumps(row) + '\n\n' for row in (
            chunk({'role': 'assistant', **delta}), chunk({}, reason))) + 'data: [DONE]\n\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(wire.encode())))
        self.end_headers()
        self.wfile.write(wire.encode())


def current_probe(requests, phase):
    assert len(requests) == 2, 'unexpected actual provider sequence'
    ident = 'observe_' + phase
    calls = [call for message in requests[-1]['messages'] if message.get('role') == 'assistant'
             for call in message.get('tool_calls', []) if call.get('id') == ident]
    assert len(calls) == 1, 'missing/duplicate current observe tool call'
    matches = [message for message in requests[-1]['messages'] if message.get('role') == 'tool'
               and message.get('tool_call_id') == ident]
    assert len(matches) == 1, 'missing/duplicate current observe tool result'
    probes = [json.loads(line[len(MARKER):]) for text in texts(matches[0].get('content'))
              for line in text.splitlines() if line.startswith(MARKER)]
    assert len(probes) == 1, 'missing/duplicate current observe marker'
    return probes[0]


def suffix(identifier, all_ids):
    normalize = lambda value: value.replace('-', '').lower()
    identifier = normalize(identifier)
    for size in range(8, len(identifier) + 1):
        candidate = identifier[-size:]
        if sum(value.endswith(candidate) for value in {normalize(value) for value in all_ids}) == 1:
            # Exercise both case and hyphen normalization, not just a substring.
            return candidate[:4].upper() + '-' + candidate[4:].upper()
    raise AssertionError('fixture did not generate a unique ID suffix')


def selected(row, expected, marker):
    for operation in ('get', 'recent'):
        value = row[operation]['value']
        assert value['agent']['activeSessionId'] == expected['active'], value
        assert value['agent']['sessionId'] == expected['durable'], value
    messages = row['recent']['value']['messages']
    assert any(message.get('role') == 'assistant' and message.get('text') == marker for message in messages), messages
    assert not any('TRANSCRIPT_SEED_FOREIGN' in message.get('text', '') for message in messages), messages
    return {'selection': 'child-a', 'transcript_marker': marker}


def rejected(row, kind, label):
    errors = [row[operation].get('error', '') for operation in ('get', 'recent')]
    assert all(errors), row  # A successful foreign/ambiguous read is never accepted.
    if kind == 'ambiguous':
        assert all('Ambiguous active session' in error for error in errors), errors
    else:
        expected = ('Agent reach is limited to parent, siblings, and children' if label == 'ts'
                    else 'is not reachable')
        assert all(expected in error for error in errors), errors
    return {'rejection': kind}


def run_case(binary, label, root, observation):
    for directory in ('home', 'tmp', 'work', 'agent', 'sessions'):
        (root / directory).mkdir(parents=True)
    root = root.resolve()
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'TZ',
        'PRIME_AGENT_KERNEL_PYTHON', 'PA_E2E_KERNEL_PYTHON') if key in os.environ}
    library = Path(os.environ['PI_PACKAGE_DIR'])
    if label == 'ts':
        env['PI_PACKAGE_DIR'] = str(library)
    env['PYTHONPATH'] = os.pathsep.join(str(path) for path in sorted((library / 'skills').glob('*/src')))
    env.update(HOME=str(root / 'home'), TMPDIR=str(root / 'tmp'), DO_NOT_TRACK='1', PI_OFFLINE='1',
        PI_CODING_AGENT_DIR=str(root / 'agent'), PRIME_AGENT_CODING_AGENT_DIR=str(root / 'agent'),
        PI_SESSION_DIR=str(root / 'sessions'), PRIME_AGENT_SESSION_DIR=str(root / 'sessions'),
        XDG_CONFIG_HOME=str(root / 'config'), XDG_CACHE_HOME=str(root / 'cache'), XDG_DATA_HOME=str(root / 'data'))
    env[TAG] = str(root)
    provider = Provider()
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    (root / 'agent/models.json').write_text(json.dumps({'providers': {'fixture': {
        'api': 'openai-completions', 'apiKey': 'synthetic-only',
        'baseUrl': 'http://127.0.0.1:%s/v1' % provider.server_port,
        'models': [{'id': 'observe-fixture', 'name': 'Observe Fixture', 'reasoning': False,
                    'contextWindow': 128000, 'maxTokens': 4096}]}}}))
    (root / 'agent/settings.json').write_text(json.dumps({'prewarmPythonKernel': False,
        'defaultProvider': 'fixture', 'defaultModel': 'observe-fixture'}))
    socket_path = root / 'd.sock'
    scope = OwnedProcesses(binary, root, socket_path)
    log = (root / 'daemon.log').open('wb')
    command = [str(binary), '--mode', 'daemon', '--daemon-socket', str(socket_path)]
    process = subprocess.Popen(command, cwd=root / 'work', env=env, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    client, primary = None, None
    done = threading.Event()
    tracker_errors = []
    def discover():
        while not done.is_set():
            try:
                scope.discover()
            except Exception as error:
                tracker_errors.append(str(error))
                return
            done.wait(0.025)
    tracker = threading.Thread(target=discover, daemon=True)
    try:
        scope.register(process, command)
        client = ObserveClient(socket_path, process)
        tracker.start()
        config = {'cwd': str(root / 'work'), 'agentDir': str(root / 'agent'),
                  'sessionDir': str(root / 'sessions'), 'provider': 'fixture', 'model': 'observe-fixture',
                  'noExtensions': True, 'noContextFiles': True, 'telemetryDisabled': True}
        created = observation['created'] = {}
        def create(name, parent=None):
            options = {'config': dict(config), 'name': name, 'lifecycle': 'resident'}
            if parent:
                options['config'].update(rlmDepth=1, parentSessionPath=parent['file'])
                options['runtimeMetadata'] = {'kind': 'subagent', 'rlmDepth': 1,
                    'rlmChildId': name, 'parentActiveSessionId': parent['active'],
                    'parentSessionId': parent['durable'], 'parentSessionFile': parent['file']}
            value = client.call('create', **options)
            row = {'active': value.get('activeSessionId') or value.get('id'),
                   'durable': value['sessionId'], 'file': value['sessionFile'], 'raw': value}
            assert row['active'], 'create omitted actual active identity'
            created[name] = row
            deadline = time.monotonic() + LIMIT
            while True:
                scope.discover()
                matches = []
                for path in (root / 'agent/daemon-workers').rglob('*.json'):
                    try:
                        descriptor = json.loads(path.read_text())
                        if descriptor.get('rootActiveSessionId') == row['active']:
                            pid, identity = verify_worker(descriptor, row['active'], binary, socket_path)
                            assert scope.ownership(pid, identity), 'worker lacked fixture ownership'
                            matches.append((pid, identity))
                    except (OSError, ValueError):
                        pass  # Atomic/transitional descriptor reads are not readiness.
                if matches:
                    assert len(matches) == 1, 'duplicate descriptor identity'
                    observation.setdefault('worker_identities', {})[name] = matches[0]
                    break
                assert time.monotonic() < deadline, 'actual worker descriptor never ready'
                threading.Event().wait(0.025)
            return row
        parent = create('observe-parent')
        a = create('observe-child-a', parent)
        b = create('observe-child-b', parent)
        foreign = create('observe-foreign')
        for name, row in (('seed_a', a), ('seed_b', b), ('seed_foreign', foreign)):
            provider.phase, provider.code = name, None
            client.call('prompt_and_wait', timeout_seconds=90, activeSessionId=row['active'], message=name)
            assert len(provider.requests[name]) == 1, 'seed did not finish in one real model turn'
        ids = [row[key] for row in created.values() for key in ('active', 'durable')]
        probes = observation['probes'] = {}
        contract = observation['contract'] = {}
        def probe(phase, selectors):
            provider.phase, provider.code = phase, cell(selectors)
            client.call('prompt_and_wait', timeout_seconds=90, activeSessionId=parent['active'],
                        message='Run the resident observe selectors fixture: ' + phase)
            value = current_probe(provider.requests[phase], phase)
            probes[phase] = value
            roster = value['roster']
            family = [roster['current']] + roster['agents']
            family_ids = {row.get('activeSessionId') for row in family}
            assert {parent['active'], a['active'], b['active']} <= family_ids, roster
            assert foreign['active'] not in family_ids, roster
            return value['selectors']
        basic = probe('baseline', {'exact': a['active'], 'active_suffix': suffix(a['active'], ids),
            'durable_suffix': suffix(a['durable'], ids), 'name': 'observe-child-a',
            'foreign_suffix': suffix(foreign['active'], ids)})
        for name in ('exact', 'active_suffix', 'durable_suffix', 'name'):
            contract[name] = selected(basic[name], a, 'TRANSCRIPT_SEED_A')
        contract['foreign_suffix'] = rejected(basic['foreign_suffix'], 'foreign-family', label)
        client.call('rename', activeSessionId=b['active'], name=a['active'])
        collision = probe('collision', {'exact_id_over_name': a['active']})
        contract['exact_id_over_name'] = selected(collision['exact_id_over_name'], a, 'TRANSCRIPT_SEED_A')
        for row in (a, b):
            client.call('rename', activeSessionId=row['active'], name='observe-duplicate')
        ambiguous = probe('ambiguity', {'duplicate_name': 'observe-duplicate'})
        contract['duplicate_name'] = rejected(ambiguous['duplicate_name'], 'ambiguous', label)
        # Selection is proven only for resident rows; no hydration or full summary equality.
        assert len(observation['worker_identities']) == 4, 'missing fixture worker proof'
        observation['summary_field_presence'] = {
            phase: {name: {operation: sorted(row[operation]['value']['agent'])
                for operation in ('get', 'recent') if 'value' in row[operation]}
                for name, row in value['selectors'].items()}
            for phase, value in probes.items()}

    except BaseException as error:
        primary = error
        observation['primary_error'] = str(error)
        raise
    finally:
        done.set()
        if tracker.ident is not None:
            tracker.join(timeout=2)
        finish(scope, client, process, observation)
        observation['requests'] = provider.requests
        observation['sent_wire'] = client.sent if client else []
        observation['received_wire'] = client.wire if client else []
        observation['tracker_errors'] = tracker_errors
        if tracker_errors or tracker.is_alive():
            observation['cleanup_verified'] = False
            observation['cleanup_error'] = 'ownership tracker failed: ' + str(tracker_errors)
        log.close()
        observation['daemon_log'] = (root / 'daemon.log').read_text(errors='replace')
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)
        if not observation['cleanup_verified'] and primary is None:
            raise AssertionError('owned observe fixture cleanup failed: ' + observation.get('cleanup_error', 'unknown'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    receipt = {'verified': False, 'runs': {}, 'scope': 'Resident agent_observe.get/recent/list: exact active ID, '
        'normalized active/durable ID suffix, exact name, exact ID/name precedence, ambiguous name, foreign family rejection.',
        'not_verified': ['Passive child hydration', 'Transient progress-note event retention',
            'latestMessage/full summary field parity', 'Tool/image/bash preview fidelity', 'TUI/ACP presentation', 'RLM spawn/settle policy'],
        'normalization': 'Dynamic IDs verified exactly inside each run, then compared by selected fixture identity. '
            'Foreign denial text differs (TS family-reach error vs Rust unreachable); raw errors are retained. '
            'All summary/preview objects remain raw and are not claimed equal.',
        'ownership': 'pa-daemon internal family resolver; existing pa-core host bridge and Python skill unchanged; no new pub API/dependencies.'}
    try:
        assert sys.platform == 'linux', 'requires hosted Linux binaries'
        assert digest(os.environ['PA_TS_ARCHIVE']) == TS_ARCHIVE_SHA256, 'TS archive SHA mismatch'
        receipt['ts_archive_sha256'] = TS_ARCHIVE_SHA256
        temp = tempfile.TemporaryDirectory(prefix='observe-selectors-')
        try:
            for label in ('ts', 'rust'):
                binary = Path(os.environ['PA_' + label.upper() + '_BINARY']).resolve()
                observation = receipt['runs'][label] = {'binary': str(binary), 'binary_sha256': digest(binary)}
                run_case(binary, label, Path(temp.name) / label, observation)
            assert receipt['runs']['ts']['contract'] == receipt['runs']['rust']['contract'], 'selector contract differs'
            receipt['summary_field_presence'] = {label: run['summary_field_presence']
                for label, run in receipt['runs'].items()}
            receipt['verified'] = True
        finally:
            primary_pending = sys.exc_info()[0] is not None
            try:
                temp.cleanup()
            except Exception as cleanup_error:
                receipt['directory_cleanup_error'] = str(cleanup_error)
                receipt['verified'] = False
                if not primary_pending:
                    raise  # Preserve an earlier probe error instead of replacing it.

    except Exception as error:
        receipt['error'] = str(error)
        receipt['verified'] = False
    finally:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print('resident observe selectors verified:', receipt['verified'])
    return 0 if receipt['verified'] else 1


if __name__ == '__main__':
    sys.exit(main())
