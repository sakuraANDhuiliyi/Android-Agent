from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import jobs
from agent.api import ConversationAskRequest, create_app
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.project import init_project
from agent.submissions import lookup_submission, submission_hash
from agent.users import UserStore
from test_workspace import IsolatedWorkspaceMixin, _api_settings


class ConversationSubmissionTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.settings = _api_settings()
        self.project = init_project('submissions', package='com.example.submit', user_id='local')
        self.conv = self.store.create_conversation('local', self.project)
        self.events = ConversationEventStore(self.store)
        self.users = UserStore(self._data / 'users.db')
        self.client = self.make_client()
        self.worker = patch('agent.jobs.start_worker').start()
        self.addCleanup(patch.stopall)

    def make_client(self, settings=None, **kwargs):
        client = TestClient(create_app(settings=settings or self.settings, task_store=self.store, user_store=self.users),
                            headers={'Authorization': 'Bearer test-token'}, **kwargs)
        self.addCleanup(client.close)
        return client

    def post(self, body=None, client=None, conv=None):
        return (client or self.client).post(f'/api/conversations/{conv or self.conv["id"]}/ask',
                                           json=body or {'prompt': 'one task', 'request_key': 'stable-key'})

    def lookup(self, key='stable-key', client=None, conv=None):
        return (client or self.client).get(f'/api/conversations/{conv or self.conv["id"]}/submissions/{key}')

    def counts(self):
        with self.store._connect() as conn:
            return [conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
                    ('tasks','conversation_turns','conversation_events','task_submissions')]

    def test_new_replay_lookup_and_distinct_keys(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        first = response.json()
        self.assertEqual(first['schema_version'], 1)
        self.assertEqual(first['submission']['job_id'], first['job']['id'])
        self.assertEqual(first['submission']['turn_id'], first['job']['turn_id'])
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().json(), first)
        self.assertEqual(self.lookup().json(), {key:first[key] for key in ('schema_version','submission','job')})
        self.assertEqual(self.worker.call_count, 1)
        self.assertEqual(self.counts(), [1,1,1,1])
        second = self.post({'prompt':'one task','request_key':'another-key'})
        self.assertEqual(second.status_code,201)
        self.assertNotEqual(second.json()['job']['id'], first['job']['id'])

    def test_parsed_defaults_match_but_every_original_semantic_field_conflicts(self):
        base={'prompt':' one task ', 'request_key':'stable-key'}
        first=self.post(base); self.assertEqual(first.status_code,201,first.text)
        full=ConversationAskRequest(**base).model_dump(mode='json')
        self.assertEqual(self.post(full).status_code,200)
        for change in ({'prompt':'one task'},{'provider':''},{'provider':'auto'}, {'provider':'openai'},
                       {'auto_fallback':True},{'run_mode':'workspace'}, {'feedback_requested':True},
                       {'contexts':[{'kind':'file','label':'x','path':'app/src/main/A.kt'}]}):
            with self.subTest(change=change): self.assertEqual(self.post({**base,**change}).status_code,409)
        other=self.store.create_conversation('local',self.project)
        self.assertEqual(self.post(base,conv=other['id']).status_code,409)
        self.assertEqual(self.lookup(conv=other['id']).status_code,404)
        self.assertEqual(self.counts(),[1,1,1,1])

    def test_secret_collision_full_context_and_order_are_not_compared_after_redaction(self):
        base={'request_key':'stable-key','prompt':'password=synthetic_secret_one','contexts':[
            {'kind':'selection','label':'A','text':'password=context_secret_one','line_start':1},
            {'kind':'file','label':'B','path':'app/src/main/B.kt'}]}
        first=self.post(base);self.assertEqual(first.status_code,201,first.text)
        self.assertNotIn('synthetic_secret_one',first.text);self.assertNotIn('context_secret_one',first.text)
        self.assertNotIn('request_hash',first.text)
        self.assertEqual(self.post(base).status_code,200)
        for field,value in [('prompt','password=synthetic_secret_two'),('contexts',list(reversed(base['contexts'])))]:
            self.assertEqual(self.post({**base,field:value}).status_code,409)
        for field,value in [('label','new'),('text','password=context_secret_two'),('path','other'),('symbol','A'),
                            ('line_start',2),('line_end',5),('ref_id','ref')]:
            changed=copy.deepcopy(base);changed['contexts'][0][field]=value
            with self.subTest(field=field):self.assertEqual(self.post(changed).status_code,409)
        with self.store._connect() as conn:
            blob=' '.join(str(tuple(row)) for row in conn.execute('SELECT * FROM tasks'))
            self.assertNotIn('synthetic_secret_one',blob);self.assertNotIn('context_secret_one',blob)

    def test_strict_keys_and_legacy_paths(self):
        for value in ('',' x','x ','-x','a.b','a/b','中文','x'*201,3,True,[],{}):
            with self.subTest(key=value):self.assertEqual(self.post({'prompt':'test','request_key':value}).status_code,422)
        for value in ('-x','a.b','x'*201): self.assertEqual(self.lookup(value).status_code,422)
        for body in ({'prompt':'legacy'},{'prompt':'legacy','request_key':None}):
            response=self.post(body);self.assertEqual(response.status_code,200,response.text)
            self.assertNotIn('submission',response.json())
        self.assertEqual(self.counts(),[2,2,2,0])
        self.assertEqual(self.client.post(f'/api/projects/{self.project}/ask',json={'prompt':'p','request_key':'k'}).status_code,422)

    def test_replay_survives_terminal_config_quota_profile_archive_and_workspace_deletion(self):
        first=self.post().json();job=first['job']['id']
        self.store.update_task(job,status='paused')
        unavailable=self.make_client(replace(self.settings,api_key='',max_prompt_chars=1,max_active_tasks_per_user=0))
        with patch('agent.api.resolve_job_settings',side_effect=AssertionError('no new resolution')), \
             patch('agent.jobs.create_semantic_checkpoint',side_effect=AssertionError('no repeated checkpoint')), \
             patch('agent.jobs._resolve_permission',side_effect=AssertionError('no new profile')):
            self.assertEqual(self.post(client=unavailable).status_code,200)
            self.assertEqual(self.lookup(client=unavailable).json()['job']['status'],'paused')
        self.store.update_task(job,status='succeeded',finished_at=time.time())
        self.store.delete_conversation(self.conv['id'],'local')
        shutil.rmtree(self._workspaces/'local'/self.project)
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.lookup().status_code,200)
        self.assertEqual(self.post({'prompt':'new','request_key':'new'}).status_code,404)
        self.assertEqual(self.worker.call_count,1)

    def test_guest_keyed_no_quota_and_foreign_scope(self):
        guest=self.make_client(replace(self.settings,guest_sessions_enabled=True))
        with patch.object(self.users,'is_guest',return_value=True), \
             patch.object(self.users,'consume_guest_message',side_effect=AssertionError('must not reserve')):
            self.assertEqual(self.post(client=guest).status_code,403)
            self.assertEqual(self.lookup(client=guest).status_code,403)
        foreign=self.store.create_conversation('other',self.project)
        self.assertEqual(self.post(conv=foreign['id']).status_code,404)
        self.assertEqual(self.lookup(conv=foreign['id']).status_code,404)
        self.assertEqual(self.counts(),[0,0,0,0])

    def test_all_initialization_failures_rollback_including_title_and_no_worker(self):
        for method in ('create_task','create_turn','append_event_idempotent'):
            owner=self.store if method=='create_task' else ConversationEventStore
            original=getattr(owner,method)
            def fail(*args,**kwargs):
                original(*args,**kwargs)
                raise RuntimeError('synthetic initialization failure')
            with self.subTest(method=method),patch.object(owner,method,new=fail):
                response=self.post();self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(self.counts(),[0,0,0,0]);self.worker.assert_not_called()
            self.assertEqual(self.store.get_conversation(self.conv['id'],'local')['title'],self.conv['title'])
        with self.store._connect() as conn:
            conn.execute("CREATE TRIGGER fail_submission BEFORE INSERT ON task_submissions BEGIN SELECT RAISE(ABORT,'fail'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.post()
        self.assertEqual(self.counts(),[0,0,0,0])

    def test_post_commit_startup_failure_retains_accepted_queued_identity(self):
        self.worker.side_effect=OSError('synthetic startup failure')
        response=self.post();self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['job']['status'],'queued')
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.lookup().status_code,200)
        self.assertEqual(self.worker.call_count,1)
        self.assertEqual(self.counts(),[1,1,1,1])

    def test_lookup_identity_scope_corruption_fails_closed_without_replacement(self):
        first=self.post().json();task=first['job']['id'];turn=first['job']['turn_id']
        for table,field,value in [('tasks','user_id','other'),('tasks','project_id','wrong'),
                ('tasks','conversation_id','wrong'),('conversation_turns','user_id','other'),
                ('conversation_turns','project_id','wrong')]:
            with self.subTest(table=table,field=field):
                with self.store._connect() as conn:
                    original=conn.execute(f'SELECT {field} FROM {table} WHERE id=?',(task if table=='tasks' else turn,)).fetchone()[0]
                    conn.execute(f'UPDATE {table} SET {field}=? WHERE id=?',(value,task if table=='tasks' else turn))
                self.assertEqual(self.lookup().status_code,404)
                self.assertEqual(self.post().status_code,409)
                with self.store._connect() as conn:
                    conn.execute(f'UPDATE {table} SET {field}=? WHERE id=?',(original,task if table=='tasks' else turn))
        with self.store._connect() as conn:conn.execute('DELETE FROM conversation_events WHERE turn_id=?',(turn,))
        self.assertEqual(self.lookup().status_code,404);self.assertEqual(self.post().status_code,409)
        self.assertEqual(self.counts(),[1,1,0,1])

    def test_actual_foreign_account_cannot_lookup_or_submit_and_same_key_is_user_scoped(self):
        self.post()
        user,token=self.users.register()
        foreign_conv=self.store.create_conversation(user,self.project)
        foreign=TestClient(self.client.app,headers={'Authorization':f'Bearer {token}'})
        self.addCleanup(foreign.close)
        self.assertEqual(self.post(client=foreign).status_code,404)
        self.assertEqual(self.lookup(client=foreign).status_code,404)
        init_project('submissions',package='com.example.foreign',user_id=user)
        response=self.post(client=foreign,conv=foreign_conv['id'])
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['job']['user_id'],user)
        self.assertEqual(self.counts(),[2,2,2,2])

    def test_guest_legacy_keeps_readonly_and_quota_rules(self):
        client=self.make_client(replace(self.settings,guest_sessions_enabled=True))
        with patch.object(self.users,'is_guest',return_value=True),patch.object(self.users,'consume_guest_message',return_value=2) as quota:
            response=self.post({'prompt':'legacy guest','run_mode':'workspace','feedback_requested':True},client=client)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['guest_remaining'],2)
        job=self.store.get_task(response.json()['job']['id'])
        self.assertEqual(job['context']['run_mode'],'read_only')
        self.assertFalse(job['context']['feedback_requested'])
        quota.assert_called_once()
        accepted_settings=self.worker.call_args.args[0]
        self.assertEqual(accepted_settings.max_auto_continuations,0)
        self.assertFalse(accepted_settings.auto_build_after_edit)
        self.assertLessEqual(accepted_settings.max_turns,3)

    def test_snapshot_dto_cannot_mix_concurrent_task_or_turn_changes(self):
        self.post();before=self.lookup().json()
        original=self.store.get_task
        def update_after_read(*args,**kwargs):
            value=original(*args,**kwargs)
            with self.store._connect() as other:
                other.execute("UPDATE tasks SET status='failed' WHERE id=?",(value['id'],))
                other.execute("UPDATE conversation_turns SET project_id='corrupt' WHERE task_id=?",(value['id'],))
            return value
        with patch.object(self.store,'get_task',side_effect=update_after_read):
            response=self.lookup()
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(),before)
        self.assertEqual(self.lookup().status_code,404)

    def test_upgrade_export_restore_immutable_ledger_and_fk_lifecycle(self):
        from agent.stores.migrate_pg import render_postgres_sql
        with self.store._connect() as conn:
            conn.execute('DROP TRIGGER immutable_task_submission')
            conn.execute('DROP TABLE task_submissions')
        TaskStore(self.store.db_path)  # Upgrade an existing DB.
        before=self.post().json()
        restored=TaskStore(self._data/'restored.db')
        sql=render_postgres_sql(self.store.db_path,schema='main')
        with restored._connect() as conn: conn.executescript(sql)
        self.assertEqual(lookup_submission(restored,'local',self.conv['id'],'stable-key'),
                         {key:before[key] for key in ('schema_version','submission','job')})
        with restored._connect() as conn:
            for statement,params in [
                ("UPDATE task_submissions SET request_hash='changed'",()),
                ('DELETE FROM tasks WHERE id=?',(before['job']['id'],)),
                ('DELETE FROM conversation_turns WHERE id=?',(before['job']['turn_id'],)),
                ('DELETE FROM conversations WHERE id=?',(self.conv['id'],)),
            ]:
                with self.subTest(sql=statement),self.assertRaises(sqlite3.IntegrityError):conn.execute(statement,params)
        self.store=restored
        client=self.make_client()
        self.assertEqual(self.post(client=client).json(),before)
        restored.purge_user_data('local')
        self.assertEqual(self.counts(),[0,0,0,0])

    def test_active_worker_cannot_claim_or_observe_partially_initialized_task(self):
        import threading
        inserted=threading.Event();release=threading.Event();claimed=threading.Event();result={}
        original=self.store.create_task
        def held(task,**kwargs):
            original(task,**kwargs);inserted.set()
            if not release.wait(5):raise AssertionError('test release timeout')
        def submit():
            try:result['response']=self.post()
            except BaseException as exc:result['error']=exc
        def claim():
            result['claimed']=self.store.claim_next_task('already-running-worker');claimed.set()
        with patch.object(self.store,'create_task',side_effect=held):
            thread=threading.Thread(target=submit);thread.start()
            consumer=None
            try:
                self.assertTrue(inserted.wait(5))
                self.assertEqual(self.counts(),[0,0,0,0])
                consumer=threading.Thread(target=claim);consumer.start()
                self.assertFalse(claimed.wait(.05))
            finally:
                release.set();thread.join(5)
                if consumer:consumer.join(5)
        self.assertFalse(thread.is_alive());self.assertFalse(consumer.is_alive())
        if 'error' in result:raise result['error']
        self.assertEqual(result['response'].status_code,201,result['response'].text)
        self.assertEqual(result['claimed']['id'],result['response'].json()['job']['id'])
        self.assertEqual(self.counts(),[1,1,1,1])
        turn=self.events.get_turn_by_task(result['claimed']['id'])
        self.assertEqual(len(self.events.list_turn_events(turn['id'])),1)

    def contract_samples(self):
        from types import SimpleNamespace
        self.conv=self.store.create_conversation('local',self.project,conversation_id='conv-submission')
        values=iter(['job-sub-001','trace-sub-001','turn-sub-001','event-sub-001','outbox-sub-001'])
        with patch('agent.jobs.uuid.uuid4',side_effect=lambda:SimpleNamespace(hex=next(values))),patch('agent.jobs.time.time',return_value=1700000000.0):
            created=self.post({'prompt':'为主页添加欢迎文字','request_key':'request-submission-001'})
        self.assertEqual(created.status_code,201,created.text)
        body={'prompt':'为主页添加欢迎文字','request_key':'request-submission-001'}
        samples={'conversation_submission_201.json':created.json(),
                 'conversation_submission_lookup_200.json':self.lookup('request-submission-001').json()}
        task=created.json()['job']['id']
        self.store.update_task(task,status='paused')
        samples['conversation_submission_paused_200.json']=self.post(body).json()
        self.store.update_task(task,status='succeeded',finished_at=1700000010.0,final_message='已完成欢迎文字')
        samples['conversation_submission_200.json']=self.post(body).json()
        return samples

    def test_shared_fixtures_are_actual_http_responses(self):
        fixtures=Path(__file__).parent/'fixtures'/'api_contract'
        for filename,value in self.contract_samples().items():
            with self.subTest(fixture=filename):self.assertEqual(value,json.loads((fixtures/filename).read_text()))

    def test_legacy_project_invalid_conversation_keeps_conflict_and_guest_refund(self):
        other=init_project('other',package='com.example.other',user_id='local')
        conv=self.store.create_conversation('local',other)
        client=self.make_client(replace(self.settings,guest_sessions_enabled=True))
        with patch.object(self.users,'is_guest',return_value=True), \
             patch.object(self.users,'consume_guest_message',return_value=2), \
             patch.object(self.users,'refund_guest_message') as refund:
            for cid in ('missing',conv['id']):
                with self.subTest(conversation=cid):
                    response=client.post(f'/api/projects/{self.project}/ask',json={'prompt':'legacy','conversation_id':cid})
                    self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(refund.call_count,2)
        self.assertEqual(self.counts(),[0,0,0,0])

    def test_corrupt_accepted_json_and_timestamps_do_not_return_adoptable_receipts(self):
        first=self.post().json();task=first['job']['id']
        with self.store._connect() as conn:conn.execute('DROP TRIGGER immutable_task_submission')
        for table,field,value in [('tasks','changed_files','broken'),('tasks','changed_files','{}'),('tasks','context_json','broken'),
                                 ('tasks','context_json','[]'),('task_submissions','created_at',0),
                                 ('task_submissions','created_at','broken'),('task_submissions','created_at',float('inf')),
                                 ('conversation_events','payload_json','broken')]:
            with self.subTest(table=table,field=field,value=value):
                with self.store._connect() as conn:
                    prior=conn.execute(f'SELECT {field} FROM {table}').fetchone()[0]
                    conn.execute(f'UPDATE {table} SET {field}=?',(value,))
                self.assertEqual(self.lookup().status_code,404)
                self.assertEqual(self.post().status_code,409)
                with self.store._connect() as conn:conn.execute(f'UPDATE {table} SET {field}=?',(prior,))
        self.assertEqual(self.counts(),[1,1,1,1]);self.assertEqual(self.worker.call_count,1)

    def child_process(self, *, key='stable-key', prompt='one task', mode='normal', limit=6, release=None, marker=None):
        script=r'''
import json,os,sys,time
from pathlib import Path
from dataclasses import replace
from agent import jobs
from agent.api import ConversationAskRequest
from agent.conversation_events import ConversationEventStore
from agent.database import TaskStore
from agent.submissions import submission_hash,SubmissionConflict,SubmissionQuotaExceeded
from test_workspace import _api_settings
store=TaskStore(Path(sys.argv[1])); project,conv,key,prompt,mode=sys.argv[2:7]
limit=int(sys.argv[7]); release,marker=map(Path,sys.argv[8:10])
jobs.configure_task_store(store)
jobs.start_worker=lambda *args:None
settings=replace(_api_settings(),max_active_tasks_per_user=limit)
body=ConversationAskRequest(prompt=prompt,request_key=key).model_dump(mode='json',exclude={'request_key'})
digest=submission_hash('local',project,conv,body)
if mode.startswith('crash-'):
    if mode=='crash-task': owner,name=store,'create_task'
    elif mode=='crash-turn': owner,name=ConversationEventStore,'create_turn'
    elif mode=='crash-event': owner,name=ConversationEventStore,'append_event_idempotent'
    elif mode=='crash-ledger': owner,name=store,'get_task'
    else: owner,name=jobs,'start_worker'
    original=getattr(owner,name)
    def crash(*args,**kwargs):
        result=original(*args,**kwargs)
        os._exit(73)
    setattr(owner,name,crash)
marker.write_text('ready')
while not release.exists():
    time.sleep(.005)
try:
    result,created=jobs.start_conversation_submission('local',project,conv,prompt,settings,
        request_key=key,request_hash=digest)
    print(json.dumps({'created':created,'job_id':result['job']['id'],'submission':result['submission']}))
except (SubmissionConflict,SubmissionQuotaExceeded) as exc:
    print(json.dumps({'error':type(exc).__name__}))
'''
        release=release or self._data/'release'
        marker=marker or self._data/f'ready-{mode}-{key}'
        env={**os.environ,'PYTHONPATH':str(Path.cwd()/'tests')+os.pathsep+str(Path.cwd()),
             'AGENT_DATA_DIR':str(self._data),'AGENT_WORKSPACES_DIR':str(self._workspaces),
             'AGENT_BUILDS_DIR':str(self._builds)}
        proc=subprocess.Popen([sys.executable,'-c',script,str(self.store.db_path),self.project,self.conv['id'],key,prompt,mode,
                               str(limit),str(release),str(marker)],env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.addCleanup(lambda: (proc.kill(),proc.communicate()) if proc.poll() is None else None)
        return proc,marker

    def wait_markers(self, markers):
        deadline=time.monotonic()+15
        while not all(path.exists() for path in markers):
            if time.monotonic()>deadline:self.fail('child startup timed out')
            time.sleep(.01)

    def process_result(self, proc):
        stdout,stderr=proc.communicate(timeout=15)
        self.assertEqual(proc.returncode,0,stderr)
        return json.loads(stdout)

    def test_multiple_processes_same_key_race_and_distinct_key_quota(self):
        release=self._data/'release'
        children=[self.child_process(release=release,marker=self._data/f'ready-{n}') for n in range(4)]
        self.wait_markers([m for p,m in children]);release.touch()
        results=[self.process_result(p) for p,m in children]
        self.assertEqual(sum(r['created'] for r in results),1)
        self.assertEqual(len({r['job_id'] for r in results}),1)
        self.assertEqual(self.counts(),[1,1,1,1])
        self.store.update_task(results[0]['job_id'],status='succeeded')
        release.unlink()
        children=[self.child_process(key=f'quota-{n}',limit=1,release=release,marker=self._data/f'quota-ready-{n}') for n in range(2)]
        self.wait_markers([m for p,m in children]);release.touch()
        results=[self.process_result(p) for p,m in children]
        self.assertEqual(sum(r.get('created',False) for r in results),1)
        self.assertEqual([r.get('error') for r in results].count('SubmissionQuotaExceeded'),1)
        self.assertEqual(self.counts(),[2,2,2,2])

    def test_multiple_processes_same_key_different_original_bodies_only_one_wins(self):
        release=self._data/'release'
        children=[self.child_process(prompt=prompt,release=release,marker=self._data/f'ready-{n}')
                  for n,prompt in enumerate(('first original','second original'))]
        self.wait_markers([m for p,m in children]);release.touch()
        results=[self.process_result(p) for p,m in children]
        self.assertEqual(sum(r.get('created',False) for r in results),1)
        self.assertEqual([r.get('error') for r in results].count('SubmissionConflict'),1)
        self.assertEqual(self.counts(),[1,1,1,1])

    def test_process_exit_rolls_back_each_initialization_stage_and_commit_survives_lost_response(self):
        release=self._data/'release';release.touch()
        for stage in ('task','turn','event','ledger'):
            with self.subTest(stage=stage):
                proc,marker=self.child_process(mode='crash-'+stage,release=release)
                stdout,stderr=proc.communicate(timeout=15)
                self.assertEqual(proc.returncode,73,stderr)
                TaskStore(self.store.db_path)
                self.assertEqual(self.counts(),[0,0,0,0])
                self.assertEqual(self.lookup().status_code,404)
        proc,marker=self.child_process(mode='crash-commit',release=release)
        stdout,stderr=proc.communicate(timeout=15);self.assertEqual(proc.returncode,73,stderr)
        self.assertEqual(self.counts(),[1,1,1,1])
        accepted=self.lookup().json()
        response=self.post();self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['submission'],accepted['submission'])
        self.worker.assert_not_called()

    def test_permission_and_execution_selection_preserved_without_credentials(self):
        from agent.project_settings import update_project_settings
        from agent.paths import workspace_path
        update_project_settings(workspace_path('local',self.project),permission_profile='safe')
        attachment={'kind':'selection','label':'selected code','text':'class Home {}','path':'app/src/Home.kt','line_start':1}
        response=self.post({'prompt':'read only','request_key':'profile','contexts':[attachment],'feedback_requested':True})
        self.assertEqual(response.status_code,201,response.text)
        task=self.store.get_task(response.json()['job']['id'])
        self.assertEqual(task['context']['permission_profile'],'safe')
        self.assertEqual(task['context']['run_mode'],'read_only')
        self.assertEqual(task['context']['attachments'],[attachment])
        self.assertTrue(task['context']['feedback_requested'])
        self.assertIn('model_selection',task['context'])
        self.assertNotIn('api_key',json.dumps(task['context']))
        explicit=self.post({'prompt':'explicit','request_key':'explicit','run_mode':'ask'}).json()['job']
        self.assertEqual(explicit['run_mode'],'ask')
        self.assertNotIn('permission_profile',explicit)

    def test_title_update_failure_rolls_back_ledger_and_legacy_initializer_is_atomic(self):
        with self.store._connect() as conn:
            conn.execute("CREATE TRIGGER fail_title BEFORE UPDATE OF title ON conversations BEGIN SELECT RAISE(ABORT,'fail title'); END")
        for body in ({'prompt':'keyed','request_key':'stable-key'}, {'prompt':'legacy'}):
            with self.subTest(body=body),self.assertRaises(sqlite3.IntegrityError):self.post(body)
            self.assertEqual(self.counts(),[0,0,0,0])
        self.worker.assert_not_called()
        with self.store._connect() as conn:conn.execute('DROP TRIGGER fail_title')
        response=self.post({'prompt':'legacy'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.counts(),[1,1,1,0])

    def test_hash_known_encoding_defaults_and_replay_after_serialization_loss(self):
        import hashlib
        body=ConversationAskRequest(prompt='中文 \n',contexts=[{'kind':'file','label':'f'}])
        parsed=body.model_dump(mode='json',exclude={'request_key'})
        self.assertIsNone(parsed['contexts'][0]['path'])
        expected=hashlib.sha256(json.dumps(['conversation_ask_v1','local',self.project,self.conv['id'],parsed],
            sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        self.assertEqual(submission_hash('local',self.project,self.conv['id'],parsed),expected)
        # The DB commit precedes DTO/response serialization: a response failure
        # must leave the key bound for the next exact request.
        original=jobs.job_to_dict
        def fail_after_commit(*args,**kwargs):
            original(*args,**kwargs)
            raise OSError('synthetic serialization outage')
        with patch('agent.jobs.job_to_dict',side_effect=fail_after_commit),self.assertRaises(OSError):self.post()
        self.assertEqual(self.counts(),[1,1,1,1])
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.worker.call_count,1)
