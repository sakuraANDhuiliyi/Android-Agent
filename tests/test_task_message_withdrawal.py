from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore, TaskMessageConflict
from agent.project import init_project
from agent.task_messages import dispatch_follow_ups, enqueue_follow_up, message_receipt, withdraw_follow_up
from agent.users import UserStore
from test_workspace import IsolatedWorkspaceMixin, _api_settings


class TaskMessageWithdrawalTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.settings = _api_settings()
        self.project = init_project('withdrawals', package='com.example.receipts', user_id='local')
        self.conv = self.store.create_conversation('local', self.project)
        self.events = ConversationEventStore(self.store)
        self.store.create_task(dict(id='source', user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='original', status='queued', created_at=time.time(),
            provider=self.settings.provider, model=self.settings.model,
            context={'run_mode': 'read_only', 'permission_profile': 'safe'}))
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

    def withdraw(self, message, task='source', client=None):
        return (client or self.client).post(f'/api/jobs/{task}/messages/{message["id"]}/withdraw')

    def test_http_withdraw_retry_and_original_send_retry_never_revive(self):
        body = {'message_key': 'stable', 'type': 'follow_up', 'payload': {'text': 'later'}}
        admitted = self.client.post('/api/jobs/source/messages', json=body).json()['message']
        self.assertTrue(admitted['can_withdraw'])
        first = self.withdraw(admitted)
        self.assertEqual(first.status_code, 200, first.text)
        receipt = first.json()['message']
        self.assertEqual(receipt['delivery_state'], 'withdrawn')
        self.assertFalse(receipt['can_withdraw'])
        self.assertGreater(receipt['withdrawn_at'], 0)
        for key in ('consumed_at', 'context_message_id', 'follow_up_job_id', 'follow_up_turn_id', 'reason'):
            self.assertIsNone(receipt[key])
        self.finish()
        repeated = self.withdraw(admitted)
        self.assertEqual(repeated.json(), first.json())
        sending_again = self.client.post('/api/jobs/source/messages', json=body)
        self.assertEqual(sending_again.status_code, 200)
        self.assertEqual(sending_again.json()['message'], receipt)
        changed = {**body, 'payload': {'text': 'different'}}
        self.assertEqual(self.client.post('/api/jobs/source/messages', json=changed).status_code, 409)
        restarted = TaskStore(self.store.db_path)
        dispatch_follow_ups(restarted, self.settings)
        self.assertEqual(len(restarted.list_tasks('local', self.project)), 1)
        self.assertEqual(self.client.get('/api/jobs/source/messages').json()['messages'], [])
        self.assertEqual(restarted.get_pending_messages('source'), [])
        self.assertFalse(restarted.consume_message(admitted['id']))
        all_rows = self.client.get('/api/jobs/source/messages?include_consumed=true').json()['messages']
        self.assertEqual(all_rows, [receipt])
        with restarted._connect() as conn:
            audit = dict(conn.execute('SELECT * FROM task_message_withdrawals').fetchone())
        self.assertEqual(audit, {'message_id': admitted['id'], 'user_id': 'local', 'created_at': receipt['withdrawn_at']})

    def test_pending_paused_and_blocked_sources_can_withdraw_without_starting(self):
        for status, turn_status in [('running', 'running'), ('paused', 'paused'), ('failed', 'failed'), ('canceled', 'canceled'), ('failed', 'interrupted')]:
            with self.subTest(status=status, turn_status=turn_status):
                message = self.add(f'{status}-{turn_status}', 'follow_up')
                self.finish(status=status, turn_status=turn_status)
                before = self.store.get_task('source')
                self.assertTrue(message_receipt(self.store, message, 'local')['can_withdraw'])
                self.assertEqual(self.withdraw(message).status_code, 200)
                self.assertEqual(self.store.get_task('source'), before)
                self.assertEqual(len(self.store.list_tasks('local', self.project)), 1)

    def test_wrong_job_owner_message_and_guest_are_rejected_without_charge(self):
        from dataclasses import replace
        from fastapi.testclient import TestClient
        from agent.api import create_app
        message = self.add(kind='follow_up')
        self.assertEqual(self.withdraw(message, 'missing').status_code, 404)
        self.assertEqual(self.withdraw({'id': message['id'] + 123}).status_code, 404)
        self.assertEqual(self.withdraw({'id': 2**100}).status_code, 404)
        self.store.create_task(dict(id='foreign', user_id='other', project_id=self.project,
            prompt='other', status='queued', created_at=time.time()))
        other = self.store.add_task_message('foreign', 'secret', 'follow_up', {'text': 'foreign instruction'})
        self.assertEqual(self.withdraw(other, 'foreign').status_code, 404)
        self.assertEqual(self.withdraw(other).status_code, 404)
        self.assertIsNone(withdraw_follow_up(self.store, 'source', message['id'], 'other'))
        client = TestClient(create_app(settings=replace(self.settings, guest_sessions_enabled=True), task_store=self.store, user_store=self.users))
        self.addCleanup(client.close)
        guest = self.users.guest_session(device={'device_id': 'withdraw-guest', 'device_name': 'Test', 'device_type': 'android'})
        with patch.object(self.users, 'consume_guest_message', side_effect=AssertionError('no guest charging')):
            self.assertEqual(client.post(f'/api/jobs/source/messages/{message["id"]}/withdraw',
                headers={'Authorization': f"Bearer {guest['token']}"}).status_code, 403)
        self.assertTrue(message_receipt(self.store, message, 'local')['can_withdraw'])

    def test_wrong_type_legacy_consumption_and_existing_child_fail_closed(self):
        for kind in ('steer', 'cancel', 'pause', 'resume'):
            with self.subTest(kind=kind):
                message = self.add(kind, kind)
                self.assertFalse(message_receipt(self.store, message, 'local')['can_withdraw'])
                self.assertEqual(self.withdraw(message).status_code, 409)
        legacy = self.add('legacy', 'follow_up')
        self.store.consume_message(legacy['id'])
        self.assertEqual(self.withdraw(legacy).status_code, 409)
        self.assertEqual(message_receipt(self.store, legacy, 'local')['delivery_state'], 'unknown')
        # A separate source is independent of the blocked legacy queue.
        self.store.create_task(dict(id='new-source', user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='new', status='succeeded', created_at=time.time()))
        self.events.create_turn(self.conv['id'], 'local', self.project, task_id='new-source', status='succeeded')
        message = self.store.add_task_message('new-source', 'created', 'follow_up', {'text':'child'})
        child = enqueue_follow_up(self.store, message['id'], self.settings)
        for status in ('queued', 'running', 'paused', 'succeeded', 'failed', 'canceled'):
            with self.subTest(child_status=status):
                self.store.update_task(child, status=status)
                before = self.store.get_task(child)
                self.assertEqual(self.withdraw(message, 'new-source').status_code, 409)
                self.assertEqual(self.store.get_task(child), before)
                receipt = message_receipt(self.store, message, 'local')
                self.assertEqual(receipt['follow_up_job_id'], child)
                self.assertFalse(receipt['can_withdraw'])

    def test_raw_mapping_with_damaged_scope_cannot_be_withdrawn(self):
        message = self.add(kind='follow_up')
        self.finish()
        child = enqueue_follow_up(self.store, message['id'], self.settings)
        with self.store._connect() as conn:
            conn.execute('UPDATE task_messages SET consumed_at=NULL WHERE id=?', (message['id'],))
            conn.execute('UPDATE tasks SET user_id=? WHERE id=?', ('other', child))
        before = self.store.get_task(child)
        receipt = message_receipt(self.store, message, 'local')
        self.assertEqual(receipt['delivery_state'], 'unknown')
        self.assertFalse(receipt['can_withdraw'])
        self.assertIsNone(receipt['follow_up_job_id'])
        self.assertEqual(self.withdraw(message).status_code, 409)
        self.assertIsNone(enqueue_follow_up(self.store, message['id'], self.settings))
        self.assertEqual(self.store.get_task(child), before)

    def test_fifo_skips_withdrawn_head_middle_and_tail(self):
        messages = [self.add(f'item-{n}', 'follow_up', f'instruction {n}') for n in range(5)]
        for index in (0, 2, 4):
            self.assertEqual(self.withdraw(messages[index]).status_code, 200)
        self.finish()
        dispatch_follow_ups(self.store, self.settings)
        first = message_receipt(self.store, messages[1], 'local')['follow_up_job_id']
        self.assertTrue(first)
        self.assertEqual(self.store.get_task(first)['context']['run_mode'], 'read_only')
        self.assertEqual(self.store.get_task(first)['context']['permission_profile'], 'safe')
        self.assertIsNone(enqueue_follow_up(self.store, messages[3]['id'], self.settings))
        self.finish(first)
        dispatch_follow_ups(TaskStore(self.store.db_path), self.settings)
        second = message_receipt(self.store, messages[3], 'local')['follow_up_job_id']
        self.assertTrue(second)
        self.assertGreaterEqual(self.store.get_task(second)['created_at'], self.store.get_task(first)['finished_at'])
        self.assertEqual({row['prompt'] for row in self.store.list_tasks('local', self.project)},
                         {'original', 'instruction 1', 'instruction 3'})
        for index in (0, 2, 4):
            self.assertEqual(message_receipt(self.store, messages[index], 'local')['delivery_state'], 'withdrawn')

    def test_withdrawn_tail_does_not_hide_failed_created_predecessor(self):
        first, middle, last = [self.add(str(n), 'follow_up') for n in range(3)]
        self.finish()
        child = enqueue_follow_up(self.store, first['id'], self.settings)
        self.assertEqual(self.withdraw(middle).status_code, 200)
        self.finish(child, 'failed')
        dispatch_follow_ups(self.store, self.settings)
        receipt = message_receipt(self.store, last, 'local')
        self.assertEqual((receipt['delivery_state'], receipt['reason'], receipt['can_withdraw']), ('blocked','parent_failed',True))
        self.assertEqual(self.withdraw(last).status_code, 200)
        self.assertEqual(self.store.get_task(child)['status'], 'failed')

    def test_withdrawal_failure_rolls_back_and_committed_tombstone_is_immutable(self):
        message = self.add(kind='follow_up')
        with patch('agent.task_messages.message_receipt', side_effect=RuntimeError('before commit')):
            with self.assertRaises(RuntimeError):
                withdraw_follow_up(self.store, 'source', message['id'], 'local')
        restarted = TaskStore(self.store.db_path)
        self.assertTrue(message_receipt(restarted, message, 'local')['can_withdraw'])
        receipt = withdraw_follow_up(restarted, 'source', message['id'], 'local')
        with restarted._connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE task_message_withdrawals SET created_at=0 WHERE message_id=?', (message['id'],))
        self.assertEqual(message_receipt(TaskStore(self.store.db_path), message, 'local'), receipt)

    def test_database_guards_keep_delivery_and_withdrawal_mutually_exclusive(self):
        created = self.add('created', 'follow_up')
        withdrawn = self.add('withdrawn', 'follow_up')
        self.finish()
        child = enqueue_follow_up(self.store, created['id'], self.settings)
        child_turn = self.events.get_turn_by_task(child)['id']
        withdraw_follow_up(self.store, 'source', withdrawn['id'], 'local')
        with self.store._connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'follow_up_not_withdrawable'):
                conn.execute('INSERT INTO task_message_withdrawals VALUES(?,?,?)', (created['id'], 'local', time.time()))
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'follow_up_withdrawn'):
                conn.execute('INSERT INTO task_message_followups VALUES(?,?,?)', (withdrawn['id'], child, child_turn))
        self.assertEqual(message_receipt(self.store, created, 'local')['delivery_state'], 'follow_up_created')
        self.assertEqual(message_receipt(self.store, withdrawn, 'local')['delivery_state'], 'withdrawn')

    def test_schema_upgrade_preserves_existing_messages_and_user_deletion_cascades(self):
        message = self.add(kind='follow_up')
        # Emulate the previous release without the new table or guards.
        with self.store._connect() as conn:
            for name in ('prevent_withdrawn_followup_creation', 'prevent_delivered_followup_withdrawal', 'immutable_task_message_withdrawal'):
                conn.execute(f'DROP TRIGGER {name}')
            conn.execute('DROP TABLE task_message_withdrawals')
        migrated = TaskStore(self.store.db_path)
        self.assertTrue(message_receipt(migrated, message, 'local')['can_withdraw'])
        withdraw_follow_up(migrated, 'source', message['id'], 'local')
        reopened = TaskStore(self.store.db_path)
        self.assertEqual(message_receipt(reopened, message, 'local')['delivery_state'], 'withdrawn')
        reopened.purge_user_data('local')
        with reopened._connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_message_withdrawals').fetchone()[0], 0)

    def test_sql_export_preserves_child_mapping_and_withdrawal_no_revival(self):
        from agent.stores.migrate_pg import render_postgres_sql
        delivered = self.add('delivered', 'follow_up')
        withdrawn = self.add('withdrawn', 'follow_up')
        self.finish()
        child = enqueue_follow_up(self.store, delivered['id'], self.settings)
        receipt = withdraw_follow_up(self.store, 'source', withdrawn['id'], 'local')
        exported = render_postgres_sql(self.store.db_path, schema='main')
        # The renderer emits portable INSERTs. Restore them into an isolated
        # SQLite schema to verify complete receipts without an external server.
        restored = TaskStore(self._data / 'restored.db')
        with restored._connect() as conn:
            conn.executescript(exported)
        self.assertEqual(message_receipt(restored, withdrawn, 'local'), receipt)
        self.assertEqual(message_receipt(restored, delivered, 'local')['follow_up_job_id'], child)
        dispatch_follow_ups(restored, self.settings)
        repeated, created = restored.admit_task_message('source', 'local', 'withdrawn', 'follow_up', {'text':'same text'})
        self.assertFalse(created)
        self.assertEqual(message_receipt(restored, repeated, 'local')['delivery_state'], 'withdrawn')
        self.assertEqual(len(restored.list_tasks('local', self.project)), 2)

    def test_other_sources_and_explicit_new_tasks_are_independent(self):
        message = self.add(kind='follow_up')
        self.withdraw(message)
        self.finish(status='canceled')
        self.store.create_task(dict(id='explicit', user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='user explicitly started this', status='queued', created_at=time.time(),
            write_lock_key=f'main:local:{self.project}'))
        self.events.create_turn(self.conv['id'], 'local', self.project, task_id='explicit', status='queued')
        other = self.store.add_task_message('explicit', 'message', 'follow_up', {'text':'another queue'})
        self.assertEqual(self.store.claim_next_task('worker')['id'], 'explicit')
        self.assertTrue(message_receipt(self.store, other, 'local')['can_withdraw'])
        self.finish('explicit')
        self.assertTrue(enqueue_follow_up(self.store, other['id'], self.settings))
        self.assertEqual(message_receipt(self.store, message, 'local')['delivery_state'], 'withdrawn')

    def test_secret_payload_remains_redacted_in_withdrawal_and_retry(self):
        message = self.add(kind='follow_up', text='password=synthetic_withdraw_secret')
        first = self.withdraw(message)
        self.assertEqual(first.status_code, 200)
        for response in (first, self.withdraw(message), self.client.get('/api/jobs/source/messages?include_consumed=true')):
            self.assertNotIn('synthetic_withdraw_secret', response.text)
            self.assertNotIn('request_hash', response.text)

    def test_damaged_mapping_is_not_a_candidate_or_a_barrier_to_other_sources(self):
        bad = self.add('bad', 'follow_up')
        tail = self.add('tail', 'follow_up')
        self.finish()
        child = enqueue_follow_up(self.store, bad['id'], self.settings)
        with self.store._connect() as conn:
            conn.execute('UPDATE task_messages SET consumed_at=NULL WHERE id=?', (bad['id'],))
            conn.execute('UPDATE tasks SET user_id=? WHERE id=?', ('other', child))
        self.store.create_task(dict(id='independent', user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='independent', status='succeeded', created_at=time.time()))
        self.events.create_turn(self.conv['id'], 'local', self.project, task_id='independent', status='succeeded')
        good = self.store.add_task_message('independent', 'new', 'follow_up', {'text':'separate queue'})
        with patch('agent.task_messages.enqueue_follow_up', wraps=enqueue_follow_up) as enqueue:
            dispatch_follow_ups(self.store, self.settings)
        self.assertNotIn(bad['id'], [call.args[1] for call in enqueue.call_args_list])
        self.assertTrue(message_receipt(self.store, good, 'local')['follow_up_job_id'])
        self.assertEqual(message_receipt(self.store, tail, 'local')['reason'], 'legacy_missing_receipt')

    def test_cross_process_dispatch_race_has_both_atomic_outcomes(self):
        script = r'''
import json,sys,time
from pathlib import Path
from agent.database import TaskStore, TaskMessageConflict
from agent import jobs, task_messages
from agent.conversation_events import ConversationEventStore
from test_workspace import _api_settings
store=TaskStore(Path(sys.argv[1])); message_id=int(sys.argv[2]); action=sys.argv[3]
marker, release, entered=map(Path,sys.argv[4:7]); hold=sys.argv[7]=='hold'
jobs.load_project_meta=lambda *args: {}
def pause():
    marker.write_text('transaction-held')
    deadline=time.monotonic()+15
    while not release.exists():
        if time.monotonic()>deadline: raise RuntimeError('test release timed out')
        time.sleep(.005)
if hold and action=='withdraw':
    original=task_messages.message_receipt
    def receipt(*a,**kw):
        result=original(*a,**kw); pause(); return result
    task_messages.message_receipt=receipt
elif hold:
    original=ConversationEventStore.append_event_idempotent
    def append(self,*a,**kw):
        result=original(self,*a,**kw); pause(); return result
    ConversationEventStore.append_event_idempotent=append
entered.write_text('started')
try:
    result=(task_messages.withdraw_follow_up(store,'source',message_id,'local') if action=='withdraw'
            else task_messages.enqueue_follow_up(store,message_id,_api_settings()))
    print(json.dumps({'ok':result}))
except TaskMessageConflict:
    print(json.dumps({'conflict':True}))
'''
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(Path.cwd()), str(Path.cwd() / 'tests')])}
        for winner in ('withdraw', 'enqueue'):
            with self.subTest(winner=winner):
                message = self.add(f'race-{winner}', 'follow_up')
                self.finish()
                marker = self._data / f'{winner}.held'
                release = self._data / f'{winner}.release'
                entered = self._data / f'{winner}.entered'
                loser_entered = self._data / f'{winner}.loser-entered'
                arguments = [sys.executable, '-c', script, str(self.store.db_path), str(message['id'])]
                first = subprocess.Popen([*arguments, winner, str(marker), str(release), str(entered), 'hold'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                second = None
                try:
                    deadline = time.monotonic() + 15
                    while not marker.exists() and first.poll() is None and time.monotonic() < deadline:
                        time.sleep(.005)
                    self.assertTrue(marker.exists(), 'first process did not hold the write transaction')
                    loser = 'enqueue' if winner == 'withdraw' else 'withdraw'
                    second = subprocess.Popen([*arguments, loser, str(marker), str(release), str(loser_entered), 'no-hold'], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    # The second store initialization may itself wait on SQLite;
                    # either way it cannot observe a half committed result.
                    time.sleep(.15)
                    self.assertIsNone(second.poll())
                    release.write_text('release')
                    outputs = []
                    for proc in (first, second):
                        output, error = proc.communicate(timeout=20)
                        self.assertEqual(proc.returncode, 0, error)
                        outputs.append(json.loads(output))
                    receipt = message_receipt(TaskStore(self.store.db_path), message, 'local')
                    if winner == 'withdraw':
                        self.assertEqual(receipt['delivery_state'], 'withdrawn')
                        self.assertEqual(outputs[1], {'ok': None})
                    else:
                        self.assertEqual(receipt['delivery_state'], 'follow_up_created')
                        self.assertEqual(outputs[1], {'conflict': True})
                    with self.store._connect() as conn:
                        withdrawn = conn.execute('SELECT COUNT(*) FROM task_message_withdrawals WHERE message_id=?', (message['id'],)).fetchone()[0]
                        created = conn.execute('SELECT COUNT(*) FROM task_message_followups WHERE message_id=?', (message['id'],)).fetchone()[0]
                        self.assertEqual(withdrawn + created, 1)
                finally:
                    release.touch()
                    for proc in (first, second):
                        if proc is not None and proc.poll() is None:
                            proc.kill(); proc.communicate(timeout=10)
