from __future__ import annotations

import copy
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
from agent.database import TaskMessageConflict, TaskStore
from agent.project import init_project
from agent.task_messages import dispatch_follow_ups, edit_follow_up, enqueue_follow_up, message_page, reorder_follow_ups, withdraw_follow_up
from agent.users import UserStore
from test_workspace import IsolatedWorkspaceMixin, _api_settings


class TaskMessageReorderTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.settings = _api_settings()
        self.project = init_project('reorders', package='com.example.reorders', user_id='local')
        self.conv = self.store.create_conversation('local', self.project)
        self.events = ConversationEventStore(self.store)
        self.source()
        self.users = UserStore(self._data / 'users.db')
        self.client = TestClient(create_app(settings=self.settings, task_store=self.store, user_store=self.users),
                                 headers={'Authorization': 'Bearer test-token'})
        self.addCleanup(self.client.close)

    def source(self, task='source', status='queued'):
        self.store.create_task(dict(id=task, user_id='local', project_id=self.project, conversation_id=self.conv['id'],
            prompt='source', status=status, created_at=time.time(), provider=self.settings.provider, model=self.settings.model,
            context={'run_mode': 'read_only', 'permission_profile': 'safe', 'attachments': [{'kind': 'selection', 'text': 'keep'}]}))
        self.events.create_turn(self.conv['id'], 'local', self.project, task_id=task, status=status)

    def add(self, key, task='source', kind='follow_up'):
        return self.store.add_task_message(task, key, kind, {'text': key})['id']

    def finish(self, task='source', status='succeeded', turn_status=None):
        self.store.update_task(task, status=status, finished_at=time.time())
        self.events.update_turn_status(self.events.get_turn_by_task(task)['id'], turn_status or status, user_id='local')

    def page(self, task='source'):
        response = self.client.get(f'/api/jobs/{task}/messages?include_consumed=true')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def request(self, ids, key='order', task='source', version=None):
        return dict(reorder_key=key, expected_version=version or self.page(task)['queue']['version'], message_ids=ids)

    def post(self, body, task='source'):
        return self.client.post(f'/api/jobs/{task}/messages/reorders', json=body)

    def test_head_middle_tail_noop_and_original_identities_survive_reorder(self):
        a,b,c = [self.add(key) for key in ('A','B','C')]
        with self.store._connect() as conn:
            originals = [dict(row) for row in conn.execute('SELECT * FROM task_messages')]
        for n, ids in enumerate(([c,a,b], [b,c,a], [a,b,c], [a,b,c]), 1):
            with self.subTest(order=ids, revision=n):
                body = self.request(ids, f'order-{n}')
                result = self.post(body)
                self.assertEqual(result.status_code, 201, result.text)
                data = result.json()
                self.assertEqual(data['reorder'], dict(schema_version=1,task_id='source',reorder_key=body['reorder_key'],
                    expected_version=body['expected_version'],order_revision=n,message_ids=ids,created_at=data['reorder']['created_at']))
                self.assertEqual(data['queue']['message_ids'], ids)
                self.assertEqual(data['queue']['pending_message_ids'], ids)
                self.assertEqual(data['queue']['order_revision'], n)
                self.assertNotEqual(data['queue']['version'], body['expected_version'])
                self.assertEqual(self.post(body).status_code, 200)
                self.assertEqual(self.post(body).json(), data)
        with self.store._connect() as conn:
            self.assertEqual([dict(row) for row in conn.execute('SELECT * FROM task_messages')], originals)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM conversation_events').fetchone()[0], 0)
        original = {'message_key':'A','type':'follow_up','payload':{'text':'A'}}
        self.assertEqual(self.client.post('/api/jobs/source/messages',json=original).status_code, 200)
        self.assertNotIn('queue', self.client.post('/api/jobs/source/messages',json=original).json())

    def test_real_dispatch_consumes_latest_body_in_effective_order_and_same_permissions(self):
        from agent.conversation_context import build_provider_messages
        a,b,c = [self.add(key) for key in ('A','B','C')]
        self.post(self.request([c,a,b]))
        edit_follow_up(self.store,'source',c,'local',edit_key='edit',expected_revision=0,payload={'text':'C latest'})
        self.finish()
        expected = [(c,'C latest'),(a,'A'),(b,'B')]
        for message_id,prompt in expected:
            dispatch_follow_ups(TaskStore(self.store.db_path),self.settings)
            page = self.page()
            row = next(row for row in page['messages'] if row['id']==message_id)
            child = self.store.get_task(row['follow_up_job_id'])
            self.assertEqual(child['prompt'],prompt)
            self.assertEqual((child['context']['run_mode'],child['context']['permission_profile']),('read_only','safe'))
            self.assertEqual(child['context']['attachments'],[{'kind':'selection','text':'keep'}])
            events = self.events.list_turn_events(self.events.get_turn_by_task(child['id'])['id'])
            payload = events[0]['payload']
            self.assertEqual(payload['queue_order_revision'],1)
            self.assertEqual(payload['task_message_id'],message_id)
            for provider in ('openai','anthropic'):
                self.assertEqual(json.dumps(build_provider_messages(events,provider)).count(prompt),1)
            self.finish(child['id'])
        self.assertEqual(self.page()['queue']['pending_message_ids'],[])
        self.assertEqual(self.events.list_turn_events(self.events.get_turn_by_task('source')['id']),[])
        self.assertEqual(len(self.store.list_tasks('local',self.project)),4)

    def test_fixed_created_prefix_and_withdrawn_slots_never_move_or_change_child(self):
        a,b,c,d = [self.add(key) for key in ('A','B','C','D')]
        withdraw_follow_up(self.store,'source',b,'local')
        self.finish()
        child = enqueue_follow_up(self.store,a,self.settings)
        for status in ('queued','running','awaiting_approval','paused'):
            with self.subTest(status=status):
                self.finish(child,status)
                before = self.store.get_task(child)
                order = [d,c] if self.page()['queue']['pending_message_ids']==[c,d] else [c,d]
                response = self.post(self.request(order,status))
                self.assertEqual(response.status_code,201,response.text)
                self.assertEqual(response.json()['queue']['message_ids'],[a,b,*order])
                self.assertEqual(self.store.get_task(child),before)
                dispatch_follow_ups(self.store,self.settings)
                self.assertEqual(len(self.store.list_tasks('local',self.project)),2)
        invalid = self.request([d,c,a], 'move-created')
        self.assertEqual(self.post(invalid).status_code,409)
        self.assertEqual(self.post(self.request([c,d,b],'move-withdrawn')).status_code,409)

    def test_failure_barrier_and_late_ack_after_failure_withdrawal_and_newer_order(self):
        a,b,c = [self.add(key) for key in ('A','B','C')]
        first = self.request([c,a,b]); ack = self.post(first).json()['reorder']
        self.post(self.request([b,c,a],'second'))
        self.finish()
        child = enqueue_follow_up(self.store,b,self.settings)
        self.finish(child,'failed')
        before = self.page()
        self.assertEqual((before['queue']['reason'],before['queue']['can_reorder']),('parent_failed',False))
        self.assertTrue(before['queue']['version'].startswith('q1:'))
        self.assertEqual(before['queue']['pending_message_ids'],[c,a])
        self.assertEqual(self.post(self.request([a,c],'bypass')).status_code,409)
        dispatch_follow_ups(self.store,self.settings)
        self.assertEqual(len(self.store.list_tasks('local',self.project)),2)
        for row in before['messages']:
            if row['id'] in (a,c): self.assertEqual(row['blocking_job_id'],child)
        withdraw_follow_up(self.store,'source',a,'local')
        reopened = TaskStore(self.store.db_path)
        reply, created = reorder_follow_ups(reopened,'source','local',**first)
        self.assertFalse(created); self.assertEqual(reply['reorder'],ack)
        self.assertEqual(reply['queue']['message_ids'],[b,c,a]); self.assertEqual(reply['queue']['pending_message_ids'],[c])
        self.assertEqual(self.post({**first,'message_ids':[a,c,b]}).status_code,409)

    def test_snapshot_token_changes_for_admission_edit_withdraw_dispatch_and_gate_facts(self):
        a,b,c = [self.add(key) for key in ('A','B','C')]
        previous = self.page()['queue']
        def changed():
            nonlocal previous
            current = self.page()['queue']
            self.assertNotEqual(current['version'],previous['version'])
            self.assertEqual(current['order_revision'],0)
            self.assertEqual(self.post(dict(reorder_key='stale',expected_version=previous['version'],message_ids=current['pending_message_ids'])).status_code,409)
            previous=current
        d=self.add('D'); changed()
        edit_follow_up(self.store,'source',b,'local',edit_key='e',expected_revision=0,payload={'text':'new B'}); changed()
        withdraw_follow_up(self.store,'source',d,'local'); changed()
        self.finish(); changed()
        child=enqueue_follow_up(self.store,a,self.settings); changed()
        self.finish(child,'paused'); changed()
        self.finish(child,'failed'); changed()

    def test_withdrawn_interior_slot_and_append_are_preserved_and_heartbeat_does_not_invalidate(self):
        a,b,c,d=[self.add(key) for key in ('A','B','C','D')]
        withdraw_follow_up(self.store,'source',b,'local')
        response=self.post(self.request([d,c,a]))
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json()['queue']['message_ids'],[d,b,c,a])
        e=self.add('E')
        page=self.page()
        self.assertEqual(page['queue']['message_ids'],[d,b,c,a,e])
        self.assertEqual(page['queue']['pending_message_ids'],[d,c,a,e])
        self.assertEqual([row['id'] for row in page['messages']],[a,b,c,d,e])
        self.store.update_task('source',heartbeat_at=time.time(),lease_expires_at=time.time()+300)
        self.assertEqual(self.page()['queue']['version'],page['queue']['version'])
        self.source('independent')
        other=self.add('own task',task='independent')
        self.assertEqual(self.page('independent')['queue']['message_ids'],[other])
        self.assertEqual(self.page()['queue'],page['queue'])
        filtered=self.client.get('/api/jobs/source/messages').json()
        self.assertNotIn('queue',filtered)
        self.assertEqual([row['id'] for row in filtered['messages']],[a,c,d,e])
        self.assertEqual(self.page()['queue']['message_ids'],[d,b,c,a,e])

    def test_source_and_child_terminal_unknown_states_and_legacy_links_disable_reordering(self):
        cases=[('failed','failed',False,'parent_failed'),('canceled','canceled',False,'parent_canceled'),
            ('failed','interrupted',False,'parent_interrupted'),('running','running',True,'parent_canceled'),
            ('future','running',False,'unknown_status')]
        for kind in ('source','child'):
            for index,(status,turn_status,cancel,reason) in enumerate(cases):
                with self.subTest(kind=kind,status=status,turn_status=turn_status):
                    task=f'{kind}-{index}'; self.source(task)
                    a,b,c=[self.add(key,task=task) for key in ('A','B','C')]
                    body=self.request([c,b,a],task=task); ack=self.post(body,task).json()['reorder']
                    target=task
                    if kind=='child': self.finish(task); target=enqueue_follow_up(self.store,c,self.settings)
                    self.store.update_task(target,status=status,cancel_requested=cancel)
                    self.events.update_turn_status(self.events.get_turn_by_task(target)['id'],turn_status,user_id='local')
                    queue=self.page(task)['queue']
                    self.assertFalse(queue['can_reorder']); self.assertEqual(queue['reason'],reason)
                    self.assertEqual(queue['version'] is None,reason=='unknown_status')
                    self.assertEqual(self.post({**body,'reorder_key':'new'},task).status_code,409)
                    replay=self.post(body,task)
                    self.assertEqual(replay.status_code,200); self.assertEqual(replay.json()['reorder'],ack)
        self.store.consume_message(self.add('legacy'))
        self.add('waiting-A'); self.add('waiting-B')
        self.assertEqual(self.page()['queue']['reason'],'legacy_missing_receipt')
        self.assertIsNone(self.page()['queue']['version'])

    def test_scope_guest_strict_ids_membership_and_empty_eligibility(self):
        a,b=[self.add(key) for key in ('A','B')]
        valid=self.request([b,a])
        self.assertEqual(self.post(valid,'missing').status_code,404)
        _, token=self.users.register()
        self.assertEqual(self.client.post('/api/jobs/source/messages/reorders',json=valid,headers={'Authorization':f'Bearer {token}'}).status_code,404)
        guest=self.users.guest_session(device={'device_id':'reorders-guest','device_name':'test','device_type':'android'})
        guest_client=TestClient(create_app(settings=replace(self.settings,guest_sessions_enabled=True),task_store=self.store,user_store=self.users))
        self.addCleanup(guest_client.close)
        with patch.object(self.users,'consume_guest_message',side_effect=AssertionError('no guest charge')):
            self.assertEqual(guest_client.post('/api/jobs/source/messages/reorders',json=valid,headers={'Authorization':f"Bearer {guest['token']}"}).status_code,403)
        invalid=[{**valid,'extra':1},{**valid,'reorder_key':' '},{**valid,'reorder_key':1},
            *[{**valid,'message_ids':ids} for ids in ([a,a],[True,b],[float(a),b],[str(a),b],[0,b],[-1,b],[2**63,b],'bad')],
            *[{**valid,'expected_version':v} for v in (None,True,'q1:bad',valid['expected_version']+'\n')]]
        for body in invalid:
            with self.subTest(body=body): self.assertEqual(self.post(body).status_code,422)
        for ids in ([],[a],[a,b,999],[a,999]): self.assertEqual(self.post({**valid,'message_ids':ids}).status_code,409)
        self.source('empty'); queue=self.page('empty')['queue']
        self.assertEqual((queue['reason'],queue['message_ids'],queue['can_reorder']),('not_enough_pending',[],False))
        self.assertIsNotNone(queue['version'])

    def test_unknown_scope_status_and_corrupt_order_fail_closed_without_starving_other_sources(self):
        a,b=[self.add(key) for key in ('A','B')]
        self.finish()
        for table,column,value,key in [('tasks','status','future','source'),('conversation_turns','status','future',self.events.get_turn_by_task('source')['id']),
                ('conversations','project_id','foreign',self.conv['id'])]:
            with self.subTest(table=table):
                with self.store._connect() as conn:
                    original=conn.execute(f'SELECT {column} FROM {table} WHERE id=?',(key,)).fetchone()[0]
                    conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(value,key))
                self.assertFalse(self.page()['queue']['can_reorder']); self.assertIsNone(self.page()['queue']['version'])
                with self.store._connect() as conn: conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?',(original,key))
        self.post(self.request([b,a]))
        with self.store._connect() as conn:
            conn.execute('DROP TRIGGER immutable_task_message_queue_order')
        for snapshot in ('[999]',json.dumps([a,a]),json.dumps([b]),'[true,2]','{}','broken'):
            with self.subTest(snapshot=snapshot):
                with self.store._connect() as conn: conn.execute('UPDATE task_message_queue_orders SET full_message_ids=?',(snapshot,))
                page=self.page()
                self.assertEqual(page['queue']['reason'],'invalid_queue')
                self.assertEqual(page['queue']['message_ids'],[])
                self.assertIsNone(page['queue']['version'])
                self.assertEqual([row['payload']['text'] for row in page['messages']],['A','B'])
                self.assertIsNone(enqueue_follow_up(self.store,a,self.settings))
        self.source('other','succeeded'); other=self.add('other',task='other')
        dispatch_follow_ups(self.store,self.settings)
        self.assertEqual(self.page('other')['messages'][0]['id'],other)
        self.assertEqual(self.page('other')['messages'][0]['delivery_state'],'follow_up_created')

    def test_transaction_rollback_and_legacy_worker_marker_guard_leave_no_orphans(self):
        a,b=[self.add(key) for key in ('A','B')]
        body=self.request([b,a])
        with patch('agent.task_messages._reorder_receipt',side_effect=RuntimeError('precommit')):
            with self.assertRaisesRegex(RuntimeError,'precommit'): reorder_follow_ups(self.store,'source','local',**body)
        self.assertEqual(message_page(TaskStore(self.store.db_path),'source','local')['queue']['order_revision'],0)
        self.post(body); self.finish()
        original_append=ConversationEventStore.append_event_idempotent
        for marker in (None,0):
            with self.subTest(marker=marker):
                def legacy(events,*args,**kwargs):
                    args=list(args); payload=dict(args[4]); payload.pop('queue_order_revision',None)
                    if marker is not None: payload['queue_order_revision']=marker
                    args[4]=payload
                    return original_append(events,*args,**kwargs)
                with patch.object(ConversationEventStore,'append_event_idempotent',legacy):
                    with self.assertRaisesRegex(sqlite3.IntegrityError,'follow_up_queue_order_mismatch'):
                        enqueue_follow_up(self.store,b,self.settings)
                with self.store._connect() as conn:
                    for table,count in [('tasks',1),('conversation_turns',1),('conversation_events',0),('task_message_followups',0)]:
                        self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],count)
        with self.store._connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError,'immutable_queue_order'): conn.execute('UPDATE task_message_queue_orders SET order_revision=2')
        self.assertIsNotNone(enqueue_follow_up(self.store,b,self.settings))

    def test_upgrade_and_real_sql_restore_accept_historical_child_revision_then_preserve_latest_order(self):
        from agent.stores.migrate_pg import render_postgres_sql
        a,b,c,d=[self.add(key) for key in ('A','B','C','D')]
        with self.store._connect() as conn:
            conn.execute('DROP TRIGGER immutable_task_message_queue_order')
            conn.execute('DROP TRIGGER prevent_stale_followup_queue_order')
            conn.execute('DROP TABLE task_message_queue_orders')
        self.store=TaskStore(self.store.db_path)
        self.assertEqual(message_page(self.store,'source','local')['queue']['message_ids'],[a,b,c,d])
        first=self.request([b,a,c,d]); ack=self.post(first).json()['reorder']
        self.finish(); child=enqueue_follow_up(self.store,b,self.settings); self.finish(child)
        second=self.post(self.request([d,c,a],'r2')); self.assertEqual(second.status_code,201)
        withdraw_follow_up(self.store,'source',c,'local')
        before=message_page(self.store,'source','local')
        restored=TaskStore(self._data/'restored.db')
        sql=render_postgres_sql(self.store.db_path,schema='main')
        with restored._connect() as conn: conn.executescript(sql)
        self.assertEqual(message_page(restored,'source','local'),before)
        replay,created=reorder_follow_ups(restored,'source','local',**first)
        self.assertFalse(created); self.assertEqual(replay['reorder'],ack)
        dispatch_follow_ups(restored,self.settings)
        self.assertEqual(next(row for row in message_page(restored,'source','local')['messages'] if row['id']==d)['delivery_state'],'follow_up_created')
        restored.purge_user_data('local')
        with restored._connect() as conn: self.assertEqual(conn.execute('SELECT COUNT(*) FROM task_message_queue_orders').fetchone()[0],0)

    def test_queue_and_receipts_share_read_snapshot_during_concurrent_edit(self):
        a,b=[self.add(key) for key in ('A','B')]
        original=self.page()
        from agent import task_messages
        project=task_messages.queue_projection
        def changed(conn,parent):
            value=project(conn,parent)
            edit_follow_up(TaskStore(self.store.db_path),'source',b,'local',edit_key='concurrent',expected_revision=0,payload={'text':'new'})
            return value
        with patch('agent.task_messages.queue_projection',changed): page=self.page()
        self.assertEqual(page,original)
        current=self.page()
        self.assertNotEqual(current['queue']['version'],page['queue']['version'])
        self.assertEqual(current['messages'][1]['revision'],1)

    def contract_samples(self):
        self.store.create_conversation('local','demo',conversation_id='conv-001')
        self.store.create_task(dict(id='job-001',user_id='local',project_id='demo',conversation_id='conv-001',
            prompt='source',status='paused',created_at=1000.0))
        self.events.create_turn('conv-001','local','demo',task_id='job-001',turn_id='turn-001',status='paused',created_at=1000.0)
        with patch('agent.database.time.time',return_value=1002.0):
            a,b,c=[self.add(key,task='job-001') for key in ('Follow A','Follow B','Follow C')]
        samples={'job_messages_queue_200.json':self.page('job-001')}
        body=self.request([c,a,b],key='client-reorder-001',task='job-001')
        with patch('agent.task_messages.time.time',return_value=1007.0):
            result=self.post(body,'job-001'); self.assertEqual(result.status_code,201)
        samples['job_message_reorder_201.json']=result.json()
        with patch('agent.task_messages.time.time',return_value=1008.0):
            self.assertEqual(self.post(self.request([b,c,a],'client-reorder-002','job-001'),'job-001').status_code,201)
        samples['job_message_reorder_200.json']=self.post(body,'job-001').json()
        self.finish('job-001','canceled')
        result=self.post(body,'job-001'); self.assertEqual(result.status_code,200)
        samples['job_message_reorder_blocked_200.json']=result.json()
        return samples

    def test_shared_reorder_fixtures_match_real_http(self):
        fixtures=Path(__file__).parent/'fixtures'/'api_contract'
        for filename,actual in self.contract_samples().items():
            with self.subTest(fixture=filename):
                self.assertEqual(actual,json.loads((fixtures/filename).read_text()))

    def test_cross_process_reorder_cas_and_each_mutation_compete_atomically(self):
        script=r'''
import json,sys,time
from pathlib import Path
from agent.database import TaskStore,TaskMessageConflict
from agent import jobs,task_messages
from agent.conversation_events import ConversationEventStore
from test_workspace import _api_settings
store=TaskStore(Path(sys.argv[1])); task=sys.argv[2]; body=json.loads(sys.argv[3]); action=sys.argv[4]
marker,release=map(Path,sys.argv[5:7]); hold=sys.argv[7]=='hold'
jobs.load_project_meta=lambda *a: {}
def pause():
    marker.write_text('holding')
    deadline=time.monotonic()+15
    while not release.exists():
        if time.monotonic()>deadline: raise RuntimeError('timeout')
        time.sleep(.005)
if hold:
    if action.startswith('reorder'): owner,name=task_messages,'_reorder_receipt'
    elif action=='edit': owner,name=task_messages,'_edit_receipt'
    elif action=='withdraw': owner,name=task_messages,'message_receipt'
    elif action=='send': owner,name=TaskStore,'_insert_task_message'
    else: owner,name=ConversationEventStore,'append_event_idempotent'
    original=getattr(owner,name)
    def wrapped(*a,**kw):
        result=original(*a,**kw); pause(); return result
    setattr(owner,name,wrapped)
try:
    message=body['message_ids'][0]
    if action.startswith('reorder'):
        if action=='reorder-b': body={**body,'reorder_key':'other'}
        result=task_messages.reorder_follow_ups(store,task,'local',**body)
        print(json.dumps({'created':result[1]}))
    elif action=='send': store.admit_task_message(task,'local','new','follow_up',{'text':'new'}); print('{"ok":true}')
    elif action=='edit': task_messages.edit_follow_up(store,task,message,'local',edit_key='e',expected_revision=0,payload={'text':'edited'}); print('{"ok":true}')
    elif action=='withdraw': task_messages.withdraw_follow_up(store,task,message,'local'); print('{"ok":true}')
    else: task_messages.dispatch_follow_ups(store,_api_settings(),task_id=task); print('{"ok":true}')
except TaskMessageConflict: print('{"conflict":true}')
'''
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(Path.cwd()),str(Path.cwd()/'tests')])}
        combinations=[('reorder','send'),('send','reorder'),('reorder','edit'),('edit','reorder'),
            ('reorder','withdraw'),('withdraw','reorder'),('reorder','dispatch'),('dispatch','reorder'),
            ('reorder','reorder-b'),('reorder','reorder')]
        for index,(winner,loser) in enumerate(combinations):
            with self.subTest(winner=winner,loser=loser):
                task=f'race-{index}'; self.source(task,'succeeded' if 'dispatch' in (winner,loser) else 'queued')
                a,b=[self.add(key,task=task) for key in ('A','B')]
                body=self.request([b,a],task=task)
                marker,release=self._data/f'{index}.held',self._data/f'{index}.release'
                args=[sys.executable,'-c',script,str(self.store.db_path),task,json.dumps(body)]
                first=subprocess.Popen([*args,winner,str(marker),str(release),'hold'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                second=None
                try:
                    deadline=time.monotonic()+15
                    while not marker.exists() and first.poll() is None and time.monotonic()<deadline: time.sleep(.005)
                    self.assertTrue(marker.exists(),'winner did not hold transaction')
                    second=subprocess.Popen([*args,loser,str(marker),str(release),'go'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                    time.sleep(.1); self.assertIsNone(second.poll())
                    release.write_text('go')
                    outputs=[]
                    for proc in (first,second):
                        out,err=proc.communicate(timeout=20); self.assertEqual(proc.returncode,0,err); outputs.append(json.loads(out))
                    if loser.startswith('reorder'):
                        self.assertEqual(outputs[1],{'created':False} if winner==loser else {'conflict':True})
                    page=self.page(task)
                    self.assertEqual(page['queue']['order_revision'],int(winner=='reorder'))
                    self.assertEqual(page['queue']['message_ids'][:2],[b,a] if winner=='reorder' else [a,b])
                    if 'dispatch' in (winner,loser):
                        created=[row for row in page['messages'] if row['follow_up_job_id']]
                        self.assertEqual([row['id'] for row in created],[b if winner=='reorder' else a])
                    if 'send' in (winner,loser): self.assertEqual(len(page['queue']['message_ids']),3)
                    if 'withdraw' in (winner,loser): self.assertEqual(page['queue']['pending_message_ids'],[a])
                    if 'edit' in (winner,loser): self.assertEqual(next(row for row in page['messages'] if row['id']==b)['revision'],1)
                finally:
                    release.touch()
                    for proc in (first,second):
                        if proc is not None and proc.poll() is None: proc.kill(); proc.communicate(timeout=10)

    def test_process_crash_before_order_commit_keeps_original_and_retry_saves_once(self):
        a,b=[self.add(key) for key in ('A','B')]; body=self.request([b,a]); marker=self._data/'uncommitted-order'
        script=r'''
import json,sys,time
from pathlib import Path
from agent.database import TaskStore
from agent import task_messages
store=TaskStore(Path(sys.argv[1]))
def hold(*a,**kw):
    Path(sys.argv[3]).write_text('uncommitted')
    while True: time.sleep(.1)
task_messages._reorder_receipt=hold
task_messages.reorder_follow_ups(store,'source','local',**json.loads(sys.argv[2]))
'''
        proc=subprocess.Popen([sys.executable,'-c',script,str(self.store.db_path),json.dumps(body),str(marker)],
            env={**os.environ,'PYTHONPATH':str(Path.cwd())},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+15
            while not marker.exists() and proc.poll() is None and time.monotonic()<deadline: time.sleep(.005)
            self.assertTrue(marker.exists()); proc.kill(); proc.communicate(timeout=10)
            reopened=TaskStore(self.store.db_path)
            self.assertEqual(message_page(reopened,'source','local')['queue']['message_ids'],[a,b])
            for expected in (True,False):
                result,created=reorder_follow_ups(reopened,'source','local',**body)
                self.assertEqual(created,expected); self.assertEqual(result['queue']['message_ids'],[b,a])
        finally:
            if proc.poll() is None: proc.kill(); proc.communicate(timeout=10)
