from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import jobs
from agent.api import create_app
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore, TaskMessageConflict
from agent.project import init_project
from agent.project_lifecycle import project_deletion
from agent.task_messages import dispatch_follow_ups, enqueue_follow_up, message_receipt
from agent.users import UserStore
from agent.worker import TaskWorker
from test_workspace import IsolatedWorkspaceMixin, _api_settings


class TaskMessageDeliveryTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.settings = _api_settings()
        self.project = init_project('message-receipts', package='com.example.receipts', user_id='local')
        self.conv = self.store.create_conversation('local', self.project)
        self.events = ConversationEventStore(self.store)
        self.store.create_task(dict(id='source', user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='original', status='queued', created_at=time.time(),
            provider=self.settings.provider, model=self.settings.model,
            context={'run_mode': 'read_only', 'permission_profile': 'safe', 'attachments': [{'kind': 'selection', 'text': 'retained'}]}))
        self.turn = self.events.create_turn(self.conv['id'], 'local', self.project, task_id='source', status='queued')
        self.users = UserStore(self._data / 'users.db')
        self.client = TestClient(create_app(settings=self.settings, task_store=self.store, user_store=self.users),
                                 headers={'Authorization': 'Bearer test-token'})
        self.addCleanup(self.client.close)

    def add(self, key='message', kind='steer', text='same text'):
        return self.store.add_task_message('source', key, kind, {'text': text})

    def finish(self, task_id='source', status='succeeded', turn_status=None):
        self.store.update_task(task_id, status=status, finished_at=time.time())
        turn = self.events.get_turn_by_task(task_id)
        self.events.update_turn_status(turn['id'], turn_status or status, user_id='local', finished_at=time.time())

    def test_http_idempotency_terminal_retry_conflict_and_identity(self):
        body = {'message_key': 'stable', 'type': 'steer', 'payload': {'text': 'same text'}}
        first = self.client.post('/api/jobs/source/messages', json=body)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()['schema_version'], 1)
        self.assertEqual(first.json()['message']['delivery_state'], 'pending')
        self.events.consume_steers('source', self.turn['id'], 'local')
        self.finish()
        repeat = self.client.post('/api/jobs/source/messages', json=body)
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(repeat.json()['message']['id'], first.json()['message']['id'])
        self.assertEqual(repeat.json()['message']['delivery_state'], 'consumed')
        self.assertTrue(repeat.json()['message']['context_message_id'])
        changed = {**body, 'payload': {'text': 'changed'}}
        self.assertEqual(self.client.post('/api/jobs/source/messages', json=changed).status_code, 409)
        self.assertEqual(self.client.post('/api/jobs/source/messages', json={**body, 'message_key': 'new'}).status_code, 409)
        self.store.create_task(dict(id='foreign', user_id='other', project_id=self.project, prompt='other', status='running', created_at=time.time()))
        self.assertEqual(self.client.post('/api/jobs/foreign/messages', json=body).status_code, 404)
        self.assertEqual(self.client.get('/api/jobs/foreign/messages?include_consumed=true').status_code, 404)

    def test_same_text_distinct_keys_and_all_receipts_survive_consumption(self):
        messages = [self.add(key) for key in ('one', 'two')]
        self.assertEqual(self.events.consume_steers('source', self.turn['id'], 'local'), ['same text', 'same text'])
        self.assertEqual(self.events.consume_steers('source', self.turn['id'], 'local'), [])
        self.assertEqual(self.client.get('/api/jobs/source/messages').json()['messages'], [])
        rows = self.client.get('/api/jobs/source/messages?include_consumed=true').json()['messages']
        self.assertEqual([row['id'] for row in rows], [row['id'] for row in messages])
        self.assertEqual(len({row['context_message_id'] for row in rows}), 2)
        self.assertTrue(all(row['delivery_state'] == 'consumed' and row['consumed_at'] for row in rows))
        for provider in ('openai', 'anthropic'):
            from agent.conversation_context import build_provider_messages
            context = build_provider_messages(self.events.list_turn_events(self.turn['id']), provider,
                current_user_prompt='original', current_turn_id=self.turn['id'])
            self.assertEqual(json.dumps(context).count('same text'), 2)

    def test_pending_steer_terminal_and_legacy_unknown_are_explicit(self):
        message = self.add()
        self.finish()
        self.assertEqual(message_receipt(self.store, message, 'local')['delivery_state'], 'unapplied')
        self.store.consume_message(message['id'])
        legacy = message_receipt(self.store, message, 'local')
        self.assertEqual((legacy['delivery_state'], legacy['reason']), ('unknown', 'legacy_missing_receipt'))

    def test_follow_up_fifo_atomic_mapping_and_failure_blocks_tail(self):
        first, second = self.add('one', 'follow_up', 'first'), self.add('two', 'follow_up', 'second')
        self.assertIsNone(enqueue_follow_up(self.store, first['id'], self.settings))
        self.finish()
        dispatch_follow_ups(self.store, self.settings)
        one = message_receipt(self.store, first, 'local')
        two = message_receipt(self.store, second, 'local')
        self.assertEqual(one['delivery_state'], 'follow_up_created')
        self.assertEqual(two['delivery_state'], 'pending')
        child = self.store.get_task(one['follow_up_job_id'])
        self.assertEqual(child['context']['run_mode'], 'read_only')
        self.assertEqual(child['context']['permission_profile'], 'safe')
        self.assertEqual(child['context']['attachments'], [{'kind': 'selection', 'text': 'retained'}])
        self.assertEqual(child['write_lock_key'], f'main:local:{self.project}')
        self.assertEqual(child['conversation_id'], self.conv['id'])
        self.assertEqual(enqueue_follow_up(self.store, first['id'], self.settings), child['id'])
        self.finish(child['id'], 'failed')
        dispatch_follow_ups(self.store, self.settings)
        self.assertEqual(message_receipt(self.store, second, 'local')['delivery_state'], 'blocked')
        self.assertEqual(message_receipt(self.store, second, 'local')['reason'], 'parent_failed')
        self.assertEqual(len(self.store.list_tasks('local', self.project)), 2)

    def test_follow_up_only_dispatches_second_after_first_succeeded(self):
        first, second = self.add('one', 'follow_up', 'first'), self.add('two', 'follow_up', 'second')
        self.finish()
        first_id = enqueue_follow_up(self.store, first['id'], self.settings)
        self.assertIsNone(enqueue_follow_up(self.store, second['id'], self.settings))
        self.finish(first_id)
        dispatch_follow_ups(self.store, self.settings)
        second_id = message_receipt(self.store, second, 'local')['follow_up_job_id']
        self.assertTrue(second_id)
        self.assertNotEqual(first_id, second_id)
        self.assertGreaterEqual(self.store.get_task(second_id)['created_at'], self.store.get_task(first_id)['finished_at'])
        self.finish(status='failed')
        self.assertEqual(message_receipt(self.store, first, 'local')['follow_up_job_id'], first_id)

    def test_follow_up_rollback_then_restart_scan_is_complete(self):
        message = self.add(kind='follow_up')
        self.finish()
        with patch.object(ConversationEventStore, 'append_event_idempotent', side_effect=RuntimeError('simulated crash before commit')):
            with self.assertRaises(RuntimeError):
                enqueue_follow_up(self.store, message['id'], self.settings)
        self.assertEqual(len(self.store.list_tasks('local', self.project)), 1)
        self.assertEqual(len(self.store.get_pending_messages('source')), 1)
        restarted = TaskStore(self.store.db_path)
        dispatch_follow_ups(restarted, self.settings)
        receipt = message_receipt(restarted, message, 'local')
        self.assertEqual(receipt['delivery_state'], 'follow_up_created')
        self.assertTrue(receipt['consumed_at'])
        event = ConversationEventStore(restarted).list_turn_events(receipt['follow_up_turn_id'])[0]
        self.assertEqual((event['role'], event['event_type']), ('user', 'user_message'))

    def test_parent_failure_cancel_and_interruption_remain_blocked_after_restart(self):
        message = self.add(kind='follow_up')
        for status, turn_status, reason in [('failed', 'failed', 'parent_failed'), ('canceled', 'canceled', 'parent_canceled'), ('failed', 'interrupted', 'parent_interrupted')]:
            with self.subTest(reason=reason):
                self.finish(status=status, turn_status=turn_status)
                restarted = TaskStore(self.store.db_path)
                dispatch_follow_ups(restarted, self.settings)
                receipt = message_receipt(restarted, message, 'local')
                self.assertEqual((receipt['delivery_state'], receipt['reason']), ('blocked', reason))
                self.assertEqual(len(restarted.list_tasks('local', self.project)), 1)

    def test_admission_and_follow_up_cross_process_uniqueness(self):
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(Path.cwd()), str(Path.cwd() / 'tests')])}
        admission = '''import sys,json
from pathlib import Path
from agent.database import TaskStore
s=TaskStore(Path(sys.argv[1])); m,created=s.admit_task_message('source','local','concurrent','follow_up',{'text':'once'})
print(json.dumps([m['id'],created]))
'''
        processes = [subprocess.Popen([sys.executable, '-c', admission, str(self.store.db_path)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        results = []
        for proc in processes:
            output, error = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, error)
            results.append(json.loads(output))
        self.assertEqual(len({row[0] for row in results}), 1)
        self.assertEqual(sum(row[1] for row in results), 1)
        self.finish()
        message_id = results[0][0]
        enqueue = '''import sys,json
from pathlib import Path
from agent.database import TaskStore
from agent.task_messages import enqueue_follow_up
from test_workspace import _api_settings
from agent import jobs
jobs.load_project_meta=lambda *args: {}
s=TaskStore(Path(sys.argv[1])); print(enqueue_follow_up(s,int(sys.argv[2]),_api_settings()))
'''
        processes = [subprocess.Popen([sys.executable, '-c', enqueue, str(self.store.db_path), str(message_id)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        children = []
        for proc in processes:
            output, error = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, error)
            children.append(output.strip())
        self.assertEqual(len(set(children)), 1)
        self.assertNotEqual(children[0], 'None')
        self.assertEqual(len(self.store.list_tasks('local', self.project)), 2)

    def test_guest_messages_remain_forbidden_without_charging(self):
        config = replace(self.settings, guest_sessions_enabled=True)
        client = TestClient(create_app(settings=config, task_store=self.store, user_store=self.users))
        self.addCleanup(client.close)
        guest = self.users.guest_session(device={'device_id': 'synthetic-guest', 'device_name': 'Tests', 'device_type': 'android'})
        body = {'message_key': 'guest', 'type': 'steer', 'payload': {'text': 'hello'}}
        with patch.object(self.users, 'consume_guest_message', side_effect=AssertionError('must not charge guest')):
            for _ in range(2):
                self.assertEqual(client.post('/api/jobs/source/messages', json=body, headers={'Authorization': f"Bearer {guest['token']}"}).status_code, 403)

    def test_deleted_project_and_sensitive_payload_are_guarded(self):
        message = self.add(kind='follow_up')
        self.finish()
        with project_deletion('local', self.project):
            with self.assertRaises(RuntimeError):
                enqueue_follow_up(self.store, message['id'], self.settings)
        self.assertEqual(len(self.store.list_tasks('local', self.project)), 1)
        self.store.update_task('source', status='running')
        payload = {'text': 'password=synthetic_message_secret'}
        created, _ = self.store.admit_task_message('source', 'local', 'redacted', 'steer', payload)
        self.assertNotIn('synthetic_message_secret', json.dumps(created['payload']))
        same, fresh = self.store.admit_task_message('source', 'local', 'redacted', 'steer', payload)
        self.assertFalse(fresh)
        self.assertEqual(same['id'], created['id'])
        with self.assertRaises(TaskMessageConflict):
            self.store.admit_task_message('source', 'local', 'redacted', 'steer', {'text': 'password=different_synthetic_secret'})

    def test_http_redaction_keeps_original_body_retry_authoritative(self):
        body = {'message_key': 'redacted-http', 'type': 'steer',
                'payload': {'text': 'password=synthetic_original_password'}}
        first = self.client.post('/api/jobs/source/messages', json=body)
        second = self.client.post('/api/jobs/source/messages', json=body)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()['message']['id'], second.json()['message']['id'])
        for response in (first, second, self.client.get('/api/jobs/source/messages?include_consumed=true')):
            self.assertNotIn('synthetic_original_password', response.text)
            self.assertNotIn('request_hash', response.text)
        changed = {**body, 'payload': {'text': 'password=synthetic_different_password'}}
        self.assertEqual(self.client.post('/api/jobs/source/messages', json=changed).status_code, 409)

    def test_new_messages_validate_text_and_cancel_admission_but_keep_retries(self):
        for index, payload in enumerate(({}, {'text': ''}, {'text': '   '}, {'text': 12}, {'text': 'x' * 100001})):
            with self.subTest(payload_kind=index):
                response = self.client.post('/api/jobs/source/messages', json={
                    'message_key': f'invalid-{index}', 'type': 'steer', 'payload': payload})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.store.list_task_messages('source', include_consumed=True), [])
        body = {'message_key': 'before-cancel', 'type': 'steer', 'payload': {'text': 'once'}}
        self.assertEqual(self.client.post('/api/jobs/source/messages', json=body).status_code, 201)
        self.store.request_cancel('source', 'local')
        self.assertEqual(self.client.post('/api/jobs/source/messages', json=body).status_code, 200)
        self.assertEqual(self.client.post('/api/jobs/source/messages', json={**body, 'message_key': 'after-cancel'}).status_code, 409)

    def test_first_model_request_consumes_queued_steer_for_both_providers(self):
        from agent.loop import _run_anthropic, _run_openai_compatible
        from test_conversation_integration import settings, openai_response, anthropic_response
        for provider, runner in [('openai', _run_openai_compatible), ('anthropic', _run_anthropic)]:
            with self.subTest(provider=provider):
                message = self.add(provider, text=f'first request {provider}')
                response = (openai_response(text='understood') if provider == 'openai' else
                    anthropic_response([SimpleNamespace(type='text', text='understood')],
                                       stop_reason='end_turn', response_id='final'))
                fallback = 'agent.loop._chat_completion_with_fallback' if provider == 'openai' else 'agent.loop._anthropic_message_with_fallback'
                with (
                    patch.dict(sys.modules, {
                        'openai': SimpleNamespace(OpenAI=lambda **kwargs: object()),
                        'anthropic': SimpleNamespace(Anthropic=lambda **kwargs: object())}),
                    patch(fallback, return_value=(response, 'fake-model')) as model,
                    patch('agent.loop._openai_tools', return_value=[]),
                    patch('agent.loop.get_tool_definitions', return_value=[]),
                    patch('agent.loop.run_hooks'),
                ):
                    runner(settings(provider), self._workspaces, 'local', self.project, 'original',
                        'system', None, None, [], task_id='source', turn_id=self.turn['id'],
                        get_steers=lambda: self.events.consume_steers('source', self.turn['id'], 'local'))
                self.assertEqual(model.call_count, 1)
                request = model.call_args.kwargs['messages']
                matching = [item for item in request if item.get('content') == f'first request {provider}']
                self.assertEqual([item['role'] for item in matching], ['user'])
                self.assertEqual(message_receipt(self.store, message, 'local')['delivery_state'], 'consumed')

    def test_steer_atomic_failure_leaves_no_false_receipt(self):
        message = self.add()
        with patch.object(self.events, 'append_event_idempotent', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                self.events.consume_steers('source', self.turn['id'], 'local')
        self.assertEqual(message_receipt(self.store, message, 'local')['delivery_state'], 'pending')
        self.assertEqual(self.events.list_turn_events(self.turn['id']), [])
        self.assertEqual(self.events.consume_steers('source', self.turn['id'], 'local'), ['same text'])
        self.assertEqual(len(self.events.list_turn_events(self.turn['id'])), 1)

    def test_committed_follow_up_survives_restart_before_claim_and_runs_once(self):
        message = self.add(kind='follow_up', text='child instruction')
        self.finish()
        child_id = enqueue_follow_up(self.store, message['id'], self.settings)
        restarted = TaskStore(self.store.db_path)
        effects = self._data / 'follow-up-effects.txt'

        def execute(*args):
            self.assertEqual((args[0], args[5], args[13]), (child_id, 'child instruction', 'read_only'))
            with effects.open('a') as stream:
                stream.write('executed\n')

        worker = TaskWorker(restarted, execute, self.settings)
        self.assertEqual(worker.run_once()['id'], child_id)
        self.assertIsNone(worker.run_once())
        self.assertEqual(effects.read_text(), 'executed\n')
        self.assertEqual(message_receipt(restarted, message, 'local')['follow_up_job_id'], child_id)

    def test_follow_up_respects_writer_lock_and_conversation_scope(self):
        message = self.add(kind='follow_up')
        self.finish()
        self.store.create_task(dict(id='writer', user_id='local', project_id=self.project,
            prompt='writer', status='queued', created_at=time.time(),
            write_lock_key=f'main:local:{self.project}'))
        writer = self.store.claim_next_task('other-worker')
        self.assertEqual(writer['id'], 'writer')
        child = enqueue_follow_up(self.store, message['id'], self.settings)
        self.assertIsNone(self.store.claim_next_task('child-worker'))
        self.store.release_task('writer', 'other-worker', 'succeeded', claim_token=writer['claim_token'])
        self.assertEqual(self.store.claim_next_task('child-worker')['id'], child)

        extra = self.add('bad-scope', 'follow_up')
        with self.store._connect() as conn:
            conn.execute('UPDATE conversations SET user_id=? WHERE id=?', ('other', self.conv['id']))
        with self.assertRaises(ValueError):
            enqueue_follow_up(self.store, extra['id'], self.settings)
        self.assertIsNone(message_receipt(self.store, extra, 'local')['follow_up_job_id'])

    def test_legacy_rows_retry_without_hash_but_do_not_invent_link(self):
        message = self.add(kind='follow_up')
        with self.store._connect() as conn:
            conn.execute('UPDATE task_messages SET request_hash=NULL,consumed_at=? WHERE id=?', (time.time(), message['id']))
        self.finish()
        repeated, created = self.store.admit_task_message('source', 'local', 'message', 'follow_up', {'text': 'same text'})
        self.assertFalse(created)
        self.assertEqual(repeated['id'], message['id'])
        self.assertEqual(message_receipt(self.store, repeated, 'local')['delivery_state'], 'unknown')
        self.assertIsNone(enqueue_follow_up(self.store, message['id'], self.settings))

    def test_http_text_aliases_enter_durable_user_context(self):
        for alias in ('text', 'prompt', 'content'):
            body = {'message_key': alias, 'type': 'steer', 'payload': {alias: f'{alias} instruction'}}
            self.assertEqual(self.client.post('/api/jobs/source/messages', json=body).status_code, 201)
        self.assertEqual(self.events.consume_steers('source', self.turn['id'], 'local'),
                         ['text instruction', 'prompt instruction', 'content instruction'])
        rows = self.client.get('/api/jobs/source/messages?include_consumed=true').json()['messages']
        self.assertEqual([row['delivery_state'] for row in rows], ['consumed'] * 3)

    def test_provider_fallback_reloads_consumed_steers_and_completed_tools(self):
        from agent.loop import run_agent, _new_message_id
        from agent.tools import ToolResult
        from test_conversation_integration import settings, openai_response, openai_tool_call, anthropic_response
        for primary in ('openai', 'anthropic'):
            for completed_tool in (False, True):
                with self.subTest(primary=primary, completed_tool=completed_tool):
                    task_id = f'{primary}-{completed_tool}'
                    conversation = self.store.create_conversation('local', self.project)
                    self.store.create_task(dict(id=task_id, user_id='local', project_id=self.project,
                        conversation_id=conversation['id'], prompt='original instruction', status='running', created_at=time.time()))
                    turn = self.events.create_turn(conversation['id'], 'local', self.project, task_id=task_id, status='running')
                    self.events.append_event(conversation['id'], turn['id'], 'user_message',
                        {'message_id': f'prompt:{task_id}', 'content': 'original instruction'},
                        task_id=task_id, role='user', context_visible=True)
                    messages = [self.store.add_task_message(task_id, key, 'steer', {'text': 'same queued instruction'})
                                for key in ('one', 'two')]
                    secondary = 'anthropic' if primary == 'openai' else 'openai'
                    config = settings(primary, provider_fallbacks=[settings(secondary)])
                    tool_call = (openai_response(tool_calls=[openai_tool_call('effect', 'write_file', '{"path":"effect.txt","content":"done"}')], finish_reason='tool_calls')
                        if primary == 'openai' else anthropic_response([
                            SimpleNamespace(type='tool_use', id='effect', name='write_file', input={'path':'effect.txt','content':'done'})],
                            stop_reason='tool_use', response_id='effect-response'))
                    final = (openai_response(text='finished') if secondary == 'openai' else anthropic_response([
                        SimpleNamespace(type='text', text='finished')], stop_reason='end_turn', response_id='final-response'))
                    primary_name = 'agent.loop._chat_completion_with_fallback' if primary == 'openai' else 'agent.loop._anthropic_message_with_fallback'
                    secondary_name = 'agent.loop._chat_completion_with_fallback' if secondary == 'openai' else 'agent.loop._anthropic_message_with_fallback'
                    calls = []

                    def primary_response(*args, **kwargs):
                        calls.append(kwargs)
                        if completed_tool and len(calls) == 1:
                            return tool_call, 'fake-model'
                        queued_turn = self.events.create_turn(conversation['id'], 'local', self.project)
                        self.events.append_event(conversation['id'], queued_turn['id'], 'user_message',
                            {'content': 'future queued prompt must stay separate'}, role='user', context_visible=True)
                        raise RuntimeError('connection reset by provider')
                    effects = self._data / f'{task_id}-effects.txt'

                    def execute(*args, **kwargs):
                        with effects.open('a') as stream:
                            stream.write('effect-completed\n')
                        return ToolResult(True, 'effect-completed')

                    def persist(kind, payload):
                        if kind in {'assistant_message', 'tool_call', 'tool_result'}:
                            self.events.append_event(conversation['id'], turn['id'], kind, payload,
                                task_id=task_id, role='assistant' if kind == 'assistant_message' else 'tool', context_visible=True)

                    with (
                        patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=lambda **kwargs: object()),
                            'anthropic': SimpleNamespace(Anthropic=lambda **kwargs: object())}),
                        patch(primary_name, side_effect=primary_response),
                        patch(secondary_name, return_value=(final, 'fake-model')) as fallback,
                        patch('agent.loop.build_system_prompt', return_value=('system', None)),
                        patch('agent.loop.get_mcp_manager'), patch('agent.loop.run_hooks'),
                        patch('agent.loop._openai_tools', return_value=[]), patch('agent.loop.get_tool_definitions', return_value=[]),
                        patch('agent.loop.dispatch_tool', side_effect=execute) as dispatch,
                    ):
                        result = run_agent(config, self._workspaces, 'local', self.project, 'original instruction',
                            on_event=persist, task_id=task_id, turn_id=turn['id'],
                            conversation_events=self.events.list_events(conversation['id'], user_id='local'),
                            get_conversation_events=lambda: self.events.list_events(conversation['id'], user_id='local'),
                            get_steers=lambda: self.events.consume_steers(task_id, turn['id'], 'local'))
                    self.assertEqual(result, 'finished')
                    self.assertEqual(fallback.call_count, 1)
                    request = fallback.call_args.kwargs
                    rendered = json.dumps(request['messages'])
                    self.assertEqual(rendered.count('original instruction'), 1)
                    self.assertEqual(rendered.count('same queued instruction'), 2)
                    self.assertNotIn('future queued prompt', rendered)
                    self.assertEqual(dispatch.call_count, int(completed_tool))
                    self.assertEqual(request['message_id'], _new_message_id(turn['id'], secondary, 2 if completed_tool else 1))
                    if completed_tool:
                        self.assertIn('effect-completed', rendered)
                        self.assertEqual(effects.read_text(), 'effect-completed\n')
                    for message in messages:
                        receipt = message_receipt(self.store, message, 'local')
                        self.assertEqual(receipt['delivery_state'], 'consumed')
                    self.assertEqual(len([event for event in self.events.list_turn_events(turn['id'])
                                          if event['event_type'] == 'user_message']), 3)
