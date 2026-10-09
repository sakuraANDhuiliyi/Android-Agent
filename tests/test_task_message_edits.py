from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore, TaskMessageConflict
from agent.project import init_project
from agent.task_messages import dispatch_follow_ups, edit_follow_up, enqueue_follow_up, message_receipt, withdraw_follow_up
from agent.users import UserStore
from test_workspace import IsolatedWorkspaceMixin, _api_settings


class TaskMessageEditTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.settings = _api_settings()
        self.project = init_project('edits', package='com.example.receipts', user_id='local')
        self.conv = self.store.create_conversation('local', self.project)
        self.events = ConversationEventStore(self.store)
        self.source()
        self.users = UserStore(self._data / 'users.db')
        self.client = TestClient(create_app(settings=self.settings, task_store=self.store, user_store=self.users),
                                 headers={'Authorization': 'Bearer test-token'})
        self.addCleanup(self.client.close)

    def source(self, task='source', status='queued'):
        self.store.create_task(dict(id=task, user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='original source', status=status, created_at=time.time(),
            provider=self.settings.provider, model=self.settings.model,
            context={'run_mode': 'read_only', 'permission_profile': 'safe',
                     'attachments': [{'kind': 'selection', 'text': 'retained attachment'}]}))
        self.events.create_turn(self.conv['id'], 'local', self.project, task_id=task, status=status)

    def add(self, key='message', kind='follow_up', text='original follow-up', task='source'):
        return self.store.add_task_message(task, key, kind, {'text': text})

    def finish(self, task='source', status='succeeded', turn_status=None):
        self.store.update_task(task, status=status, finished_at=time.time())
        turn = self.events.get_turn_by_task(task)
        self.events.update_turn_status(turn['id'], turn_status or status, user_id='local', finished_at=time.time())

    def edit(self, message, key='edit-1', revision=0, text='updated follow-up', task=None, client=None):
        return (client or self.client).post(f'/api/jobs/{task or message["task_id"]}/messages/{message["id"]}/edits',
            json={'edit_key': key, 'expected_revision': revision, 'payload': {'text': text}})

    def test_http_edit_retains_original_send_hash_and_fifo_identity(self):
        original = {'message_key': 'stable-send', 'type': 'follow_up', 'payload': {'text': 'original text'}}
        first = self.client.post('/api/jobs/source/messages', json=original).json()['message']
        self.assertEqual((first['revision'], first['edited_at'], first['can_edit']), (0, None, True))
        with self.store._connect() as conn:
            original_row = dict(conn.execute('SELECT * FROM task_messages WHERE id=?', (first['id'],)).fetchone())
        saved = self.edit(first)
        self.assertEqual(saved.status_code, 201, saved.text)
        data = saved.json()
        self.assertEqual(data['edit'], dict(schema_version=1, task_id='source', message_id=first['id'], edit_key='edit-1',
            expected_revision=0, revision=1, created_at=data['message']['edited_at'], payload={'text': 'updated follow-up'}))
        self.assertEqual(data['message']['created_at'], first['created_at'])
        self.assertEqual(data['message']['id'], first['id'])
        self.assertEqual(data['message']['revision'], 1)
        self.assertEqual(self.edit(first).status_code, 200)
        self.assertEqual(self.edit(first).json(), data)
        replay = self.client.post('/api/jobs/source/messages', json=original)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()['message'], data['message'])
        self.assertEqual(self.client.post('/api/jobs/source/messages', json={**original, 'payload': {'text': 'updated follow-up'}}).status_code, 409)
        with self.store._connect() as conn:
            self.assertEqual(dict(conn.execute('SELECT * FROM task_messages WHERE id=?', (first['id'],)).fetchone()), original_row)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_message_revisions').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM conversation_events').fetchone()[0], 0)
        self.assertEqual(self.client.get('/api/jobs/source/messages?include_consumed=true').json()['messages'], [data['message']])

    def test_immutable_old_ack_survives_newer_revision_and_child_creation(self):
        message = self.add()
        first = self.edit(message).json()
        second = self.edit(message, 'edit-2', 1, 'newest').json()
        late = self.edit(message)
        self.assertEqual(late.status_code, 200)
        self.assertEqual(late.json()['edit'], first['edit'])
        self.assertEqual(late.json()['message'], second['message'])
        self.finish()
        child = enqueue_follow_up(TaskStore(self.store.db_path), message['id'], self.settings)
        self.assertEqual(self.store.get_task(child)['prompt'], 'newest')
        late = self.edit(message)
        self.assertEqual(late.status_code, 200)
        self.assertEqual(late.json()['edit'], first['edit'])
        self.assertEqual(late.json()['message']['follow_up_job_id'], child)
        self.assertEqual(late.json()['message']['revision'], 2)
        self.assertFalse(late.json()['message']['can_edit'])
        self.assertEqual(self.edit(message, 'new-edit', 2).status_code, 409)
        self.assertEqual(len(self.store.list_tasks('local', self.project)), 2)

    def test_shared_edit_fixtures_match_live_http_ack_and_current_state(self):
        import copy
        fixtures = Path(__file__).parent / 'fixtures' / 'api_contract'
        expected = json.loads((fixtures / 'job_message_edit_201.json').read_text())
        with patch('agent.database.time.time', return_value=1002.0):
            message = self.add(expected['message']['message_key'])
        self.finish()
        expected['job_id'] = expected['message']['task_id'] = expected['edit']['task_id'] = 'source'
        expected['message']['id'] = expected['edit']['message_id'] = message['id']
        with patch('agent.task_messages.time.time', return_value=1007.0):
            saved = self.edit(message, expected['edit']['edit_key'], 0, expected['edit']['payload']['text'])
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json(), expected)
        later = json.loads((fixtures / 'job_message_edit_200.json').read_text())
        with patch('agent.task_messages.time.time', return_value=1008.0):
            self.edit(message, 'client-edit-002', 1, later['message']['payload']['text'])
        with patch('agent.task_messages.time.time', return_value=1009.0):
            for name in ('job_message_edit_200.json', 'job_message_edit_withdrawn_200.json'):
                if 'withdrawn' in name:
                    withdraw_follow_up(self.store, 'source', message['id'], 'local')
                projected = copy.deepcopy(json.loads((fixtures / name).read_text()))
                projected['job_id'] = projected['message']['task_id'] = projected['edit']['task_id'] = 'source'
                projected['message']['id'] = projected['edit']['message_id'] = message['id']
                replay = self.edit(message, expected['edit']['edit_key'], 0, expected['edit']['payload']['text'])
                self.assertEqual(replay.status_code, 200)
                self.assertEqual(replay.json(), projected)

    def test_ack_survives_withdrawal_and_same_key_mismatch_is_always_conflict(self):
        message = self.add()
        ack = self.edit(message).json()['edit']
        withdraw_follow_up(self.store, 'source', message['id'], 'local')
        result = self.edit(message)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['edit'], ack)
        self.assertEqual(result.json()['message']['delivery_state'], 'withdrawn')
        for key, revision, text in [('edit-1', 0, 'different'), ('edit-1', 1, 'updated follow-up'), ('new-edit', 1, 'new')]:
            with self.subTest(key=key, revision=revision):
                self.assertEqual(self.edit(message, key, revision, text).status_code, 409)
        self.finish()
        dispatch_follow_ups(TaskStore(self.store.db_path), self.settings)
        self.assertEqual(len(self.store.list_tasks('local', self.project)), 1)

    def test_failed_source_preserves_ack_without_editing_or_resuming(self):
        message = self.add()
        ack = self.edit(message).json()['edit']
        for status in ('failed', 'canceled', 'interrupted'):
            with self.subTest(status=status):
                self.finish(status=status)
                before = self.store.get_task('source')
                replay = self.edit(message)
                self.assertEqual(replay.status_code, 200)
                self.assertEqual(replay.json()['edit'], ack)
                self.assertEqual(replay.json()['message']['delivery_state'], 'blocked')
                self.assertFalse(replay.json()['message']['can_edit'])
                self.assertEqual(self.edit(message, 'next', 1).status_code, 409)
                self.assertEqual(self.store.get_task('source'), before)

    def test_strict_text_only_request_rejects_unknown_or_ambiguous_values(self):
        message = self.add()
        valid = {'edit_key': 'edit', 'expected_revision': 0, 'payload': {'text': 'new'}}
        invalid = [{**valid, 'other': True}, {**valid, 'payload': {'text': 'new', 'profile': 'unsafe'}},
            {**valid, 'payload': {'prompt': 'new'}}, {**valid, 'payload': {'text': ' '}},
            {**valid, 'payload': {'text': 123}}, {**valid, 'payload': {'text': 'x' * 100_001}},
            {**valid, 'edit_key': ' '}, {**valid, 'edit_key': 'x' * 201},
            *[{**valid, 'expected_revision': value} for value in (-1, True, 0.0, '0', 2**63 - 1)]]
        for body in invalid:
            with self.subTest(body=str(body)[:150]):
                self.assertEqual(self.client.post(f'/api/jobs/source/messages/{message["id"]}/edits', json=body).status_code, 422)
        self.assertEqual(message_receipt(self.store, message, 'local')['revision'], 0)

    def test_source_states_and_preceding_child_define_authoritative_eligibility(self):
        for status, expected in [('queued', True), ('running', True), ('awaiting_approval', True), ('paused', True),
                                 ('succeeded', True), ('failed', False), ('canceled', False), ('interrupted', False), ('unknown', False)]:
            with self.subTest(status=status):
                source = f'source-{status}'
                self.source(source, status if status != 'unknown' else 'queued')
                if status == 'unknown':
                    self.store.update_task(source, status='unknown')
                message = self.add(task=source)
                self.assertEqual(message_receipt(self.store, message, 'local')['can_edit'], expected)
                self.assertEqual(self.edit(message).status_code, 201 if expected else 409)
        first, second = self.add('head'), self.add('tail')
        self.finish()
        child = enqueue_follow_up(self.store, first['id'], self.settings)
        for status in ('queued', 'running', 'awaiting_approval', 'paused', 'succeeded', 'failed', 'canceled', 'interrupted'):
            with self.subTest(child=status):
                self.finish(child, status)
                receipt = message_receipt(self.store, second, 'local')
                self.assertEqual(receipt['can_edit'], status not in {'failed', 'canceled', 'interrupted'})
                self.assertTrue(receipt['can_withdraw'])
        self.store.update_task('source', cancel_requested=True)
        self.assertFalse(message_receipt(self.store, second, 'local')['can_edit'])

    def test_scope_guest_consumed_legacy_and_raw_corrupt_mapping_fail_closed(self):
        message = self.add()
        self.assertEqual(self.edit(message, task='missing').status_code, 404)
        self.assertEqual(self.edit({**message, 'id': 2**100}).status_code, 404)
        self.assertEqual(self.edit({**message, 'id': -1}).status_code, 404)
        self.store.create_task(dict(id='foreign', user_id='other', project_id=self.project, prompt='private', status='running', created_at=time.time()))
        other = self.add(task='foreign')
        self.assertEqual(self.edit(other).status_code, 404)
        self.assertEqual(self.edit(other, task='source').status_code, 404)
        client = TestClient(create_app(settings=replace(self.settings, guest_sessions_enabled=True), task_store=self.store, user_store=self.users))
        self.addCleanup(client.close)
        guest = self.users.guest_session(device={'device_id': 'edits-guest', 'device_name': 'Test', 'device_type': 'android'})
        with patch.object(self.users, 'consume_guest_message', side_effect=AssertionError('guest not charged')):
            response = client.post(f'/api/jobs/source/messages/{message["id"]}/edits',
                headers={'Authorization': f"Bearer {guest['token']}"}, json={'edit_key': 'e', 'expected_revision': 0, 'payload': {'text': 'new'}})
            self.assertEqual(response.status_code, 403)
        for kind in ('steer', 'cancel', 'pause', 'resume'):
            with self.subTest(kind=kind):
                wrong = self.add(kind, kind)
                self.assertFalse(message_receipt(self.store, wrong, 'local')['can_edit'])
                self.assertEqual(self.edit(wrong).status_code, 409)
        self.finish()
        child = enqueue_follow_up(self.store, message['id'], self.settings)
        with self.store._connect() as conn:
            conn.execute('UPDATE task_messages SET consumed_at=NULL WHERE id=?', (message['id'],))
            conn.execute('UPDATE tasks SET user_id=? WHERE id=?', ('other', child))
        self.assertFalse(message_receipt(self.store, message, 'local')['can_edit'])
        self.assertEqual(self.edit(message).status_code, 409)
        legacy = self.add('legacy')
        self.store.consume_message(legacy['id'])
        self.assertEqual(self.edit(legacy).status_code, 409)

    def test_missing_or_foreign_conversation_prevents_new_edits(self):
        message = self.add()
        with self.store._connect() as conn:
            conn.execute('UPDATE conversations SET user_id=? WHERE id=?', ('other', self.conv['id']))
        self.assertFalse(message_receipt(self.store, message, 'local')['can_edit'])
        self.assertEqual(self.edit(message).status_code, 409)

    def test_fifo_uses_each_latest_revision_and_preserves_permissions_context(self):
        messages = [self.add(str(n), text=f'old {n}') for n in range(3)]
        for n, message in enumerate(messages):
            self.assertEqual(self.edit(message, text=f'new {n}').status_code, 201)
        self.finish()
        child_ids = []
        for n, message in enumerate(messages):
            dispatch_follow_ups(TaskStore(self.store.db_path), self.settings)
            receipt = message_receipt(self.store, message, 'local')
            child = self.store.get_task(receipt['follow_up_job_id'])
            child_ids.append(child['id'])
            self.assertEqual(child['prompt'], f'new {n}')
            self.assertEqual(child['context']['run_mode'], 'read_only')
            self.assertEqual(child['context']['permission_profile'], 'safe')
            self.assertEqual(child['context']['attachments'], [{'kind': 'selection', 'text': 'retained attachment'}])
            self.assertEqual((child['provider'], child['model']), (self.settings.provider, self.settings.model))
            for remaining in messages[n + 1:]:
                self.assertIsNone(message_receipt(self.store, remaining, 'local')['follow_up_job_id'])
            self.finish(child['id'])
        with self.store._connect() as conn:
            events = conn.execute("SELECT task_id,payload_json FROM conversation_events WHERE event_type='user_message' ORDER BY seq").fetchall()
        self.assertEqual([row['task_id'] for row in events], child_ids)
        self.assertEqual([json.loads(row['payload_json'])['task_message_revision'] for row in events], [1, 1, 1])
        self.assertEqual([json.loads(row['payload_json'])['content'][0]['text'] for row in events], ['new 0', 'new 1', 'new 2'])
        self.assertEqual(len(self.store.list_tasks('local', self.project)), 4)

    def test_only_child_provider_context_contains_latest_text_once(self):
        from agent.conversation_context import build_provider_messages
        message = self.add()
        self.edit(message, text='first draft')
        self.edit(message, 'e2', 1, 'final queued instruction')
        source_turn = self.events.get_turn_by_task('source')
        self.assertEqual(self.events.list_turn_events(source_turn['id']), [])
        self.finish()
        child = enqueue_follow_up(self.store, message['id'], self.settings)
        child_turn = self.events.get_turn_by_task(child)
        events = self.events.list_turn_events(child_turn['id'])
        for provider in ('openai', 'anthropic'):
            with self.subTest(provider=provider):
                context = build_provider_messages(events, provider)
                rendered = json.dumps(context, ensure_ascii=False)
                self.assertEqual(rendered.count('final queued instruction'), 1)
                self.assertNotIn('first draft', rendered)
                self.assertNotIn('original follow-up', rendered)
        self.assertEqual(self.events.list_turn_events(source_turn['id']), [])

    def test_other_source_and_explicit_task_are_unchanged(self):
        message = self.add()
        self.source('explicit')
        other = self.add(task='explicit')
        before = self.store.get_task('explicit')
        self.edit(message)
        self.assertEqual(self.store.get_task('explicit'), before)
        other_receipt = message_receipt(self.store, other, 'local')
        self.assertEqual((other_receipt['revision'], other_receipt['payload']), (0, {'text': 'original follow-up'}))

    def test_edit_transaction_rollback_reopen_and_immutable_history(self):
        message = self.add()
        with patch('agent.task_messages._edit_receipt', side_effect=RuntimeError('before commit')):
            with self.assertRaisesRegex(RuntimeError, 'before commit'):
                edit_follow_up(self.store, 'source', message['id'], 'local', edit_key='e', expected_revision=0, payload={'text':'new'})
        restarted = TaskStore(self.store.db_path)
        self.assertEqual(message_receipt(restarted, message, 'local')['revision'], 0)
        receipt, ack, created = edit_follow_up(restarted, 'source', message['id'], 'local', edit_key='e', expected_revision=0, payload={'text':'new'})
        self.assertTrue(created)
        reopened = TaskStore(self.store.db_path)
        self.assertEqual(message_receipt(reopened, message, 'local'), receipt)
        self.assertEqual(edit_follow_up(reopened, 'source', message['id'], 'local', edit_key='e', expected_revision=0, payload={'text':'new'}), (receipt, ack, False))
        with reopened._connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable_message_revision'):
                conn.execute('UPDATE task_message_revisions SET payload=?', ('{}',))

    def test_legacy_worker_missing_or_stale_revision_rolls_back_complete_child(self):
        message = self.add()
        self.edit(message)
        self.edit(message, 'e2', 1, 'latest')
        self.finish()
        original_append = ConversationEventStore.append_event_idempotent
        for missing in (True, False):
            with self.subTest(missing=missing):
                def legacy_append(events, *args, **kwargs):
                    args = list(args)
                    payload = dict(args[4])
                    if missing:
                        payload.pop('task_message_revision', None)
                    else:
                        payload['task_message_revision'] = 1
                    args[4] = payload
                    return original_append(events, *args, **kwargs)
                with patch('agent.task_messages._latest_revision', return_value=None), patch.object(ConversationEventStore, 'append_event_idempotent', legacy_append):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, 'follow_up_revision_mismatch'):
                        enqueue_follow_up(self.store, message['id'], self.settings)
                with self.store._connect() as conn:
                    for table, expected in [('tasks', 1), ('conversation_turns', 1), ('conversation_events', 0), ('task_message_followups', 0)]:
                        self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], expected)
                    self.assertIsNone(conn.execute('SELECT consumed_at FROM task_messages WHERE id=?', (message['id'],)).fetchone()[0])
        child = enqueue_follow_up(TaskStore(self.store.db_path), message['id'], self.settings)
        self.assertEqual(self.store.get_task(child)['prompt'], 'latest')
        self.assertEqual(enqueue_follow_up(self.store, message['id'], self.settings), child)

    def test_secret_edits_deduplicate_raw_body_without_exposing_it(self):
        message = self.add(text='password=original_synthetic_secret')
        first = self.edit(message, text='password=edited_synthetic_secret')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(self.edit(message, text='password=edited_synthetic_secret').status_code, 200)
        self.assertEqual(self.edit(message, text='password=different_synthetic_secret').status_code, 409)
        for response in (first, self.client.get('/api/jobs/source/messages?include_consumed=true')):
            self.assertNotIn('edited_synthetic_secret', response.text)
            self.assertNotIn('request_hash', response.text)
        with self.store._connect() as conn:
            row = dict(conn.execute('SELECT * FROM task_message_revisions').fetchone())
            self.assertNotIn('edited_synthetic_secret', json.dumps(row))
            self.assertEqual(len(row['request_hash']), 64)

    def test_upgrade_export_actual_restore_and_account_deletion_preserve_history(self):
        from agent.stores.migrate_pg import render_postgres_sql
        message = self.add()
        with self.store._connect() as conn:
            conn.execute('DROP TRIGGER immutable_task_message_revision')
            conn.execute('DROP TRIGGER prevent_stale_followup_revision')
            conn.execute('DROP TABLE task_message_revisions')
        self.store = TaskStore(self.store.db_path)
        self.assertEqual(message_receipt(self.store, message, 'local')['revision'], 0)
        self.edit(message)
        self.edit(message, 'e2', 1, 'latest')
        withdrawn = self.add('withdrawn')
        self.edit(withdrawn)
        withdraw_follow_up(self.store, 'source', withdrawn['id'], 'local')
        self.finish()
        child = enqueue_follow_up(self.store, message['id'], self.settings)
        before = [message_receipt(self.store, row, 'local') for row in (message, withdrawn)]
        exported = render_postgres_sql(self.store.db_path, schema='main')
        restored = TaskStore(self._data / 'restored.db')
        with restored._connect() as conn:
            conn.executescript(exported)
        self.assertEqual([message_receipt(restored, row, 'local') for row in (message, withdrawn)], before)
        receipt, ack, created = edit_follow_up(restored, 'source', message['id'], 'local', edit_key='edit-1', expected_revision=0, payload={'text': 'updated follow-up'})
        self.assertFalse(created)
        self.assertEqual((ack['revision'], receipt['revision'], receipt['follow_up_job_id']), (1, 2, child))
        repeat, created = restored.admit_task_message('source', 'local', 'message', 'follow_up', {'text': 'original follow-up'})
        self.assertFalse(created)
        self.assertEqual(message_receipt(restored, repeat, 'local')['revision'], 2)
        dispatch_follow_ups(restored, self.settings)
        self.assertEqual(len(restored.list_tasks('local', self.project)), 2)
        restored.purge_user_data('local')
        with restored._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_message_revisions').fetchone()[0], 0)

    def test_cross_process_edit_dispatch_withdraw_and_cas_races(self):
        script = r'''
import json,sys,time
from pathlib import Path
from agent.database import TaskStore, TaskMessageConflict
from agent import jobs, task_messages
from agent.conversation_events import ConversationEventStore
from test_workspace import _api_settings
store=TaskStore(Path(sys.argv[1])); task_id=sys.argv[2]; message_id=int(sys.argv[3]); action=sys.argv[4]
marker, release=map(Path,sys.argv[5:7]); hold=sys.argv[7]=='hold'
jobs.load_project_meta=lambda *args: {}
def pause():
    marker.write_text('held')
    deadline=time.monotonic()+15
    while not release.exists():
        if time.monotonic()>deadline: raise RuntimeError('release timed out')
        time.sleep(.005)
if hold and action.startswith('edit'):
    original=task_messages._edit_receipt
    def ack(*a,**kw):
        result=original(*a,**kw); pause(); return result
    task_messages._edit_receipt=ack
elif hold and action=='withdraw':
    original=task_messages.message_receipt
    def receipt(*a,**kw):
        result=original(*a,**kw); pause(); return result
    task_messages.message_receipt=receipt
elif hold:
    original=ConversationEventStore.append_event_idempotent
    def append(self,*a,**kw):
        result=original(self,*a,**kw); pause(); return result
    ConversationEventStore.append_event_idempotent=append
try:
    if action.startswith('edit'):
        result=task_messages.edit_follow_up(store,task_id,message_id,'local',edit_key=action,expected_revision=0,payload={'text':action})
    elif action=='withdraw': result=task_messages.withdraw_follow_up(store,task_id,message_id,'local')
    else: result=task_messages.enqueue_follow_up(store,message_id,_api_settings())
    print(json.dumps({'ok':result}))
except TaskMessageConflict:
    print(json.dumps({'conflict':True}))
'''
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(Path.cwd()), str(Path.cwd() / 'tests')])}
        for index, (winner, loser) in enumerate([('edit-a', 'enqueue'), ('enqueue', 'edit-a'),
                ('edit-a', 'withdraw'), ('withdraw', 'edit-a'), ('edit-a', 'edit-b'), ('edit-a', 'edit-a')]):
            with self.subTest(winner=winner, loser=loser):
                task = f'race-{index}'
                self.source(task, 'succeeded')
                message = self.add(task=task)
                marker, release = self._data / f'{index}.held', self._data / f'{index}.release'
                args = [sys.executable, '-c', script, str(self.store.db_path), task, str(message['id'])]
                first = subprocess.Popen([*args, winner, str(marker), str(release), 'hold'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                second = None
                try:
                    deadline = time.monotonic() + 15
                    while not marker.exists() and first.poll() is None and time.monotonic() < deadline:
                        time.sleep(.005)
                    self.assertTrue(marker.exists(), 'winner did not hold transaction')
                    second = subprocess.Popen([*args, loser, str(marker), str(release), 'no-hold'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    time.sleep(.15)
                    self.assertIsNone(second.poll())
                    release.write_text('go')
                    outputs = []
                    for proc in (first, second):
                        output, error = proc.communicate(timeout=20)
                        self.assertEqual(proc.returncode, 0, error)
                        outputs.append(json.loads(output))
                    receipt = message_receipt(TaskStore(self.store.db_path), message, 'local')
                    if loser.startswith('edit') and winner != loser:
                        self.assertEqual(outputs[1], {'conflict': True})
                    elif winner == loser:
                        self.assertFalse(outputs[1]['ok'][2])
                    if 'enqueue' in (winner, loser):
                        child = self.store.get_task(receipt['follow_up_job_id'])
                        self.assertEqual(child['prompt'], 'edit-a' if winner.startswith('edit') else 'original follow-up')
                    if 'withdraw' in (winner, loser):
                        self.assertEqual(receipt['delivery_state'], 'withdrawn')
                    self.assertEqual(receipt['revision'], int(winner.startswith('edit')))
                    with self.store._connect() as conn:
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_message_revisions WHERE message_id=?', (message['id'],)).fetchone()[0], receipt['revision'])
                finally:
                    release.touch()
                    for proc in (first, second):
                        if proc is not None and proc.poll() is None:
                            proc.kill(); proc.communicate(timeout=10)

    def test_process_crash_before_edit_commit_keeps_original_then_retry_commits_once(self):
        message = self.add()
        marker = self._data / 'edit-uncommitted'
        script = r'''
import sys,time
from pathlib import Path
from agent.database import TaskStore
from agent import task_messages
store=TaskStore(Path(sys.argv[1]))
def ack(*a,**kw):
    Path(sys.argv[3]).write_text('uncommitted')
    while True: time.sleep(.1)
task_messages._edit_receipt=ack
task_messages.edit_follow_up(store,'source',int(sys.argv[2]),'local',edit_key='crash',expected_revision=0,payload={'text':'new'})
'''
        env = {**os.environ, 'PYTHONPATH': str(Path.cwd())}
        proc = subprocess.Popen([sys.executable, '-c', script, str(self.store.db_path), str(message['id']), str(marker)],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(marker.exists())
            proc.kill()
            proc.communicate(timeout=10)
            reopened = TaskStore(self.store.db_path)
            self.assertEqual(message_receipt(reopened, message, 'local')['revision'], 0)
            for created in (True, False):
                result = edit_follow_up(reopened, 'source', message['id'], 'local', edit_key='crash', expected_revision=0, payload={'text':'new'})
                self.assertEqual(result[2], created)
                self.assertEqual(result[0]['revision'], 1)
        finally:
            if proc.poll() is None:
                proc.kill(); proc.communicate(timeout=10)
