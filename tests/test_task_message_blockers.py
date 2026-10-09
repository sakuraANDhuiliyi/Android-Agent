from __future__ import annotations

import copy
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.api import create_app
from agent.conversation_events import ConversationEventStore
from agent.project import init_project
from agent.task_messages import dispatch_follow_ups, enqueue_follow_up, message_receipt, withdraw_follow_up
from agent.database import TaskStore
from agent.users import UserStore
from test_workspace import IsolatedWorkspaceMixin, _api_settings


class TaskMessageBlockerTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.settings = _api_settings()
        self.project = init_project('blockers', package='com.example.receipts', user_id='local')
        self.conv = self.store.create_conversation('local', self.project)
        self.events = ConversationEventStore(self.store)
        self.source()
        self.users = UserStore(self._data / 'users.db')
        self.client = TestClient(create_app(settings=self.settings, task_store=self.store, user_store=self.users),
                                 headers={'Authorization': 'Bearer test-token'})
        self.addCleanup(self.client.close)

    def source(self, task='source', status='queued'):
        self.store.create_task(dict(id=task, user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='source', status=status, created_at=time.time(),
            provider=self.settings.provider, model=self.settings.model, context={'run_mode':'read_only','permission_profile':'safe'}))
        self.events.create_turn(self.conv['id'], 'local', self.project, task_id=task, status=status)

    def add(self, key='message', task='source', kind='follow_up'):
        return self.store.add_task_message(task, key, kind, {'text':'Focus on settings screen only'})

    def finish(self, task='source', status='succeeded', turn_status=None):
        self.store.update_task(task, status=status, finished_at=time.time())
        self.events.update_turn_status(self.events.get_turn_by_task(task)['id'], turn_status or status,
                                       user_id='local', finished_at=time.time())

    def assert_pair(self, receipt, task=None):
        pair = receipt['blocking_job_id'], receipt['blocking_turn_id']
        expected = (task, self.events.get_turn_by_task(task)['id']) if task else (None, None)
        self.assertEqual(pair, expected)

    def test_source_failures_and_cancel_requested_point_to_source_without_mutation(self):
        for status, turn_status, cancel, reason in [('failed','failed',False,'parent_failed'),
                ('canceled','canceled',False,'parent_canceled'), ('failed','interrupted',False,'parent_interrupted'),
                ('interrupted','interrupted',False,'parent_interrupted'), ('running','running',True,'parent_canceled')]:
            with self.subTest(status=status, turn_status=turn_status, cancel=cancel):
                task = f'{status}-{turn_status}-{cancel}'
                self.source(task)
                message = self.add(task=task)
                self.finish(task, status, turn_status)
                self.store.update_task(task, cancel_requested=cancel)
                before = self.store.get_task(task)
                receipt = message_receipt(self.store, message, 'local')
                self.assertEqual((receipt['delivery_state'], receipt['reason']), ('blocked', reason))
                self.assert_pair(receipt, task)
                self.assertTrue(receipt['can_withdraw'])
                self.assertFalse(receipt['can_edit'])
                target = self.client.get(f'/api/jobs/{receipt["blocking_job_id"]}').json()['job']
                self.assertEqual((target['project_id'], target['conversation_id'], target['turn_id']),
                                 (self.project, self.conv['id'], receipt['blocking_turn_id']))
                self.assertIsNone(enqueue_follow_up(self.store, message['id'], self.settings))
                self.assertEqual(self.store.get_task(task), before)

    def test_first_failed_child_in_fifo_is_reported_after_skipping_withdrawn_messages(self):
        withdrawn, first, second, third, tail = [self.add(str(n)) for n in range(5)]
        withdraw_follow_up(self.store, 'source', withdrawn['id'], 'local')
        self.finish()
        children = []
        for message in (first, second, third):
            child = enqueue_follow_up(self.store, message['id'], self.settings)
            children.append(child)
            self.finish(child)
        # Historical states can change during reconciliation: still select the
        # first blocker reached by the existing FIFO gate, never the newest.
        self.finish(children[1], 'failed')
        self.finish(children[2], 'canceled')
        before = self.store.list_tasks('local', self.project)
        receipt = message_receipt(self.store, tail, 'local')
        self.assertEqual(receipt['reason'], 'parent_failed')
        self.assert_pair(receipt, children[1])
        dispatch_follow_ups(TaskStore(self.store.db_path), self.settings)
        self.assertEqual(self.store.list_tasks('local', self.project), before)
        self.assert_pair(message_receipt(self.store, withdrawn, 'local'))
        self.assert_pair(message_receipt(self.store, second, 'local'))
        # The source gate has precedence over every child gate.
        self.finish(status='failed')
        self.assert_pair(message_receipt(self.store, tail, 'local'), 'source')

    def test_child_failure_types_and_active_waits_share_existing_gate_decision(self):
        first, tail = self.add('head'), self.add('tail')
        self.finish()
        child = enqueue_follow_up(self.store, first['id'], self.settings)
        for status, turn_status, cancel, reason in [('failed','failed',False,'parent_failed'),
                ('canceled','canceled',False,'parent_canceled'), ('failed','interrupted',False,'parent_interrupted'),
                ('running','running',True,'parent_canceled'), ('paused','paused',False,None),
                ('queued','queued',False,None), ('awaiting_approval','awaiting_approval',False,None),
                ('running','running',False,None), ('succeeded','succeeded',False,None)]:
            with self.subTest(status=status, turn_status=turn_status, cancel=cancel):
                self.finish(child, status, turn_status)
                self.store.update_task(child, cancel_requested=cancel)
                receipt = message_receipt(self.store, tail, 'local')
                self.assert_pair(receipt, child if reason else None)
                self.assertEqual(receipt['delivery_state'], 'blocked' if reason else 'pending')
                self.assertEqual(receipt['reason'], reason or 'awaiting_dispatch')
                self.assertEqual(receipt['can_edit'], reason is None)
                self.assert_pair(message_receipt(self.store, first, 'local'))

    def test_nonblocked_and_non_followup_receipts_never_expose_a_pair(self):
        queued = self.add('queued')
        for status in ('queued','running','awaiting_approval','paused','succeeded'):
            with self.subTest(status=status):
                self.finish(status=status)
                receipt = message_receipt(self.store, queued, 'local')
                self.assertEqual(receipt['delivery_state'], 'pending')
                self.assert_pair(receipt)
        for kind in ('steer','cancel','pause','resume'):
            message = self.add(kind, kind=kind)
            self.finish(status='failed')
            self.assert_pair(message_receipt(self.store, message, 'local'))
        withdrawn = withdraw_follow_up(self.store, 'source', queued['id'], 'local')
        self.assert_pair(withdrawn)
        self.assertEqual(withdrawn['delivery_state'], 'withdrawn')
        legacy = self.add('legacy')
        self.store.consume_message(legacy['id'])
        receipt = message_receipt(self.store, legacy, 'local')
        self.assertEqual(receipt['delivery_state'], 'unknown')
        self.assert_pair(receipt)

    def test_legacy_consumption_and_dangling_mapping_do_not_invent_a_blocker(self):
        first, tail = self.add('legacy'), self.add('tail')
        self.finish()
        self.store.consume_message(first['id'])
        receipt = message_receipt(self.store, tail, 'local')
        self.assertEqual((receipt['delivery_state'],receipt['reason']), ('blocked','legacy_missing_receipt'))
        self.assert_pair(receipt)
        with self.store._connect() as conn:
            conn.execute('UPDATE task_messages SET consumed_at=NULL WHERE id=?', (first['id'],))
        child = enqueue_follow_up(self.store, first['id'], self.settings)
        self.finish(child, 'failed')
        with self.store._connect() as conn:
            conn.execute('UPDATE conversation_turns SET task_id=NULL WHERE task_id=?', (child,))
        receipt = message_receipt(self.store, tail, 'local')
        self.assertEqual(receipt['reason'], 'legacy_missing_receipt')
        self.assert_pair(receipt)

    def test_child_scope_corruption_removes_navigation_without_enqueuing(self):
        first, tail = self.add('head'), self.add('tail')
        self.finish()
        child = enqueue_follow_up(self.store, first['id'], self.settings)
        self.finish(child, 'failed')
        turn = self.events.get_turn_by_task(child)['id']
        other = self.store.create_conversation('local', self.project)
        variants = [('tasks','user_id','other',child), ('tasks','project_id','other',child),
            ('tasks','conversation_id',other['id'],child), ('conversation_turns','user_id','other',turn),
            ('conversation_turns','project_id','other',turn), ('conversation_turns','conversation_id',other['id'],turn),
            ('conversation_turns','task_id',None,turn)]
        for table, column, bad, row_id in variants:
            with self.subTest(table=table, column=column):
                with self.store._connect() as conn:
                    old = conn.execute(f'SELECT {column} FROM {table} WHERE id=?',(row_id,)).fetchone()[0]
                    conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(bad,row_id))
                try:
                    receipt = message_receipt(self.store, tail, 'local')
                    self.assertEqual(receipt['delivery_state'],'blocked')
                    self.assert_pair(receipt)
                    self.assertIsNone(enqueue_follow_up(self.store, tail['id'], self.settings))
                    self.assertEqual(len(self.store.list_tasks('local',self.project)), 1 if table=='tasks' and column in {'user_id','project_id'} else 2)
                finally:
                    with self.store._connect() as conn:
                        conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(old,row_id))
        self.assert_pair(message_receipt(self.store, tail, 'local'), child)

    def test_source_conversation_and_turn_integrity_is_required_for_both_blocker_kinds(self):
        first, tail = self.add('head'), self.add('tail')
        self.finish()
        child = enqueue_follow_up(self.store, first['id'], self.settings)
        self.finish(child,'failed')
        source_turn = self.events.get_turn_by_task('source')['id']
        other = self.store.create_conversation('local',self.project)
        variants = [('conversation_turns','project_id','other',source_turn),
            ('conversation_turns','user_id','other',source_turn), ('conversation_turns','task_id',None,source_turn),
            ('conversation_turns','conversation_id',other['id'],source_turn),
            ('conversations','user_id','other',self.conv['id']), ('conversations','project_id','other',self.conv['id'])]
        for source_status in ('succeeded','failed'):
            self.finish(status=source_status)
            for table,column,bad,row_id in variants:
                with self.subTest(source_status=source_status,table=table,column=column):
                    with self.store._connect() as conn:
                        old=conn.execute(f'SELECT {column} FROM {table} WHERE id=?',(row_id,)).fetchone()[0]
                        conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(bad,row_id))
                    try:
                        receipt=message_receipt(self.store,tail,'local')
                        self.assertEqual(receipt['delivery_state'],'blocked')
                        self.assert_pair(receipt)
                    finally:
                        with self.store._connect() as conn:
                            conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(old,row_id))

    def test_existing_send_and_edit_retries_project_same_current_blocker(self):
        original={'message_key':'stable','type':'follow_up','payload':{'text':'Focus on settings screen only'}}
        message=self.client.post('/api/jobs/source/messages',json=original).json()['message']
        body={'edit_key':'e','expected_revision':0,'payload':{'text':'updated'}}
        url=f'/api/jobs/source/messages/{message["id"]}/edits'
        ack=self.client.post(url,json=body).json()['edit']
        self.finish(status='failed')
        replay=self.client.post('/api/jobs/source/messages',json=original)
        edit_replay=self.client.post(url,json=body)
        listed=self.client.get('/api/jobs/source/messages?include_consumed=true')
        self.assertEqual((replay.status_code,edit_replay.status_code,listed.status_code),(200,200,200))
        self.assertEqual(edit_replay.json()['edit'],ack)
        self.assertEqual(replay.json()['message'],edit_replay.json()['message'])
        self.assertEqual(listed.json()['messages'],[replay.json()['message']])
        self.assert_pair(replay.json()['message'],'source')
        self.assertEqual(len(self.store.list_tasks('local',self.project)),1)
        withdraw=self.client.post(f'/api/jobs/source/messages/{message["id"]}/withdraw').json()['message']
        self.assert_pair(withdraw)
        self.assertEqual(self.client.post(url,json=body).json()['message'],withdraw)

    def test_foreign_account_cannot_read_source_receipts_or_blocking_task(self):
        _, token=self.users.register()
        message=self.add()
        self.finish(status='failed')
        receipt=message_receipt(self.store,message,'local')
        for path in ('/api/jobs/source/messages?include_consumed=true',f'/api/jobs/{receipt["blocking_job_id"]}'):
            with self.subTest(path=path):
                response=self.client.get(path,headers={'Authorization':f'Bearer {token}'})
                self.assertEqual(response.status_code,404)
                self.assertNotIn(receipt['blocking_turn_id'],response.text)

    def test_shared_parent_and_child_fixtures_match_live_http(self):
        fixtures=Path(__file__).parent/'fixtures'/'api_contract'
        parent_expected=json.loads((fixtures/'job_messages_blocked_parent_200.json').read_text())
        with patch('agent.database.time.time',return_value=1002.0):
            message=self.add(parent_expected['messages'][0]['message_key'])
        self.finish(status='failed')
        parent_expected['job_id']=parent_expected['messages'][0]['task_id']='source'
        parent_expected['messages'][0].update(id=message['id'],blocking_job_id='source',blocking_turn_id=self.events.get_turn_by_task('source')['id'])
        actual = self.client.get('/api/jobs/source/messages?include_consumed=true').json()
        queue = actual.pop('queue')
        self.assertEqual(actual,parent_expected)
        self.assertEqual((queue['task_id'],queue['reason'],queue['can_reorder']),('source','parent_failed',False))
        self.source('child-source')
        head=self.add('head',task='child-source')
        with patch('agent.database.time.time',return_value=1002.0):
            tail=self.add('client-msg-004',task='child-source')
        self.finish('child-source')
        child=enqueue_follow_up(self.store,head['id'],self.settings)
        self.finish(child,'failed')
        expected=copy.deepcopy(json.loads((fixtures/'job_messages_blocked_child_200.json').read_text())['messages'][0])
        expected.update(task_id='child-source',id=tail['id'],blocking_job_id=child,blocking_turn_id=self.events.get_turn_by_task(child)['id'])
        response=self.client.get('/api/jobs/child-source/messages?include_consumed=true')
        self.assertEqual(response.json()['messages'][1],expected)

    def test_blocker_lookup_failure_is_metadata_only_for_existing_gate(self):
        first,tail=self.add('head'),self.add('tail')
        self.finish()
        child=enqueue_follow_up(self.store,first['id'],self.settings)
        self.finish(child,'failed')
        before=message_receipt(self.store,tail,'local')
        turn=self.events.get_turn_by_task(child)['id']
        # r.project_id was not checked by the old queue gate. A damaged record
        # still blocks dispatch, but must never be supplied as a navigation ID.
        with self.store._connect() as conn:
            conn.execute('UPDATE conversation_turns SET project_id=? WHERE id=?',('bad-project',turn))
        after=message_receipt(self.store,tail,'local')
        self.assertEqual({k:v for k,v in before.items() if not k.startswith('blocking_')},
                         {k:v for k,v in after.items() if not k.startswith('blocking_')})
        self.assert_pair(after)
        self.assertIsNone(enqueue_follow_up(self.store,tail['id'],self.settings))
