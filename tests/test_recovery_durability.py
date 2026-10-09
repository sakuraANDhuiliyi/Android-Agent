from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent import jobs
from agent.api import create_app
from agent.config import UserAccount
from agent.conversation_context import build_provider_messages
from agent.conversation_events import ConversationEventStore, ConversationEventType as E
from agent.database import TaskStore
from agent.paths import latest_apk_path, user_builds_dir
from agent.project import init_project
from agent.users import UserStore
from agent.worker import PauseRequested
from agent.workspace import WorkspaceRepository
from test_workspace import IsolatedWorkspaceMixin, _api_settings


class RecoveryDurabilityTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.settings = _api_settings()
        self.project = init_project('recovery', package='com.example.recovery', user_id='local')
        self.workspace = self._workspaces / 'local' / self.project
        self.events = ConversationEventStore(self.store)
        self.conv = self.store.create_conversation('local', self.project)
        self.store_patch = patch.object(jobs, '_store', self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)

    def task(self, task_id='source', status='failed', turn_status='interrupted'):
        self.store.create_task(dict(id=task_id, user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='修改文件后总结', status=status,
            provider='openai', model='fake', created_at=time.time(), context={}))
        turn = self.events.create_turn(self.conv['id'], 'local', self.project,
                                      task_id=task_id, status=turn_status)
        self.events.append_event_idempotent(self.conv['id'], turn['id'], E.USER_MESSAGE,
            f"turn:{turn['id']}:user_message", {'content': '修改文件后总结'}, role='user', context_visible=True)
        return self.store.get_task(task_id), turn

    def run_job(self, task, turn, agent):
        with patch.object(jobs, 'run_agent', side_effect=agent):
            jobs._run_job(task['id'], 'local', self.project, self.conv['id'], turn['id'],
                          task['prompt'], self.settings,
                          self.events.list_events(self.conv['id']), 0)

    def test_recovery_mapping_remains_stable_across_states_and_generations(self):
        task, turn = self.task()
        self.assertEqual(self.store.recovery_state(task['id'], 'local'),
                         {'can_recover': True, 'recovery_job_id': None})
        child, created = jobs.recover_job_with_status(task['id'], 'local', self.settings)
        self.assertTrue(created)
        for status in ('queued', 'running', 'awaiting_approval', 'paused', 'succeeded', 'failed'):
            self.store.update_task(child['id'], status=status)
            repeat, created = jobs.recover_job_with_status(task['id'], 'local', self.settings)
            self.assertFalse(created)
            self.assertEqual((repeat['id'], repeat['status']), (child['id'], status))
        child_turn = self.events.get_turn_by_task(child['id'])
        self.events.update_turn_status(child_turn['id'], 'interrupted')
        grandchild, created = jobs.recover_job_with_status(child['id'], 'local', self.settings)
        self.assertTrue(created)
        self.assertEqual(grandchild['recovery_attempt'], 2)
        self.assertEqual(grandchild['recovery_of_task_id'], task['id'])
        self.assertEqual(jobs.recover_job_with_status(task['id'], 'local', self.settings)[0]['id'], child['id'])
        self.assertEqual(self.store.recovery_state(task['id'], 'local'),
                         {'can_recover': False, 'recovery_job_id': child['id']})

    def test_recovery_lifecycle_rolls_back_when_note_cannot_persist(self):
        task, _ = self.task()
        original = ConversationEventStore.append_event_idempotent
        def fail_note(store, *args, **kwargs):
            if args[2] == E.RECOVERY_NOTE:
                raise RuntimeError('injected note storage failure')
            return original(store, *args, **kwargs)
        with patch.object(ConversationEventStore, 'append_event_idempotent', fail_note):
            with self.assertRaisesRegex(RuntimeError, 'storage failure'):
                jobs.recover_job_with_status(task['id'], 'local', self.settings)
        self.assertEqual(len(self.store.list_tasks('local')), 1)
        with self.store._connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM conversation_turns').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM conversation_events WHERE event_type='recovery_note'").fetchone()[0], 0)
        self.assertTrue(self.store.recovery_state(task['id'], 'local')['can_recover'])

    def test_legacy_mapping_backfills_once_without_deleting_duplicates(self):
        task, turn = self.task()
        for index in (1, 2):
            self.store.create_task(dict(id=f'legacy-{index}', user_id='local', project_id=self.project,
                conversation_id=self.conv['id'], prompt='resume', status='paused', created_at=time.time(),
                recovery_of_task_id=task['id'], recovery_attempt=1,
                context={'interrupted_turn_id': turn['id']}))
        reloaded = TaskStore(self.store.db_path)
        self.assertEqual(reloaded.recovery_state(task['id'], 'local')['recovery_job_id'], 'legacy-1')
        self.assertEqual(len(reloaded.list_tasks('local')), 3)
        self.assertEqual(TaskStore(self.store.db_path).recovery_state(task['id'], 'local')['recovery_job_id'], 'legacy-1')

    def test_legacy_active_branch_is_not_advertised_as_recoverable(self):
        task, _ = self.task()
        self.store.update_task(task['id'], recovery_of_task_id='legacy-root', recovery_attempt=1)
        self.store.create_task(dict(id='other-branch', user_id='local', project_id=self.project,
            conversation_id=self.conv['id'], prompt='legacy branch', status='paused',
            created_at=time.time(), recovery_of_task_id='legacy-root', recovery_attempt=2))
        self.assertEqual(self.store.recovery_state(task['id'], 'local'),
                         {'can_recover': False, 'recovery_job_id': None})
        with self.assertRaisesRegex(RuntimeError, '不是可显式恢复'):
            jobs.recover_job_with_status(task['id'], 'local', self.settings)
        self.assertEqual(len(self.store.list_tasks('local')), 2)

    def test_independent_processes_recover_exactly_once(self):
        task, _ = self.task()
        script = '''import json,sys,time,os
from pathlib import Path
from agent import jobs
from agent.database import TaskStore
from agent.config import Settings
jobs._store=TaskStore(Path(sys.argv[1]))
jobs.load_project_meta=lambda *_: {}
s=Settings(provider="openai",api_key="fake",model="fake",model_candidates=["fake"],max_turns=2,max_auto_continuations=0,max_gradle_retries=0,compact_max_chars=10000,max_output_tokens=1000,base_url="https://example.invalid",auto_build_after_edit=False,server_host="127.0.0.1",server_port=8000,api_token="")
Path(sys.argv[2]+"."+str(os.getpid())+".ready").touch()
while not Path(sys.argv[2]).exists(): time.sleep(.01)
r,created=jobs.recover_job_with_status("source","local",s)
print(json.dumps({"id":r["id"],"created":created}))
'''
        gate = self._data / 'go'
        processes = [subprocess.Popen([sys.executable, '-c', script, str(self.store.db_path), str(gate)],
                        cwd=str(Path(__file__).resolve().parents[1]),
                        env=dict(os.environ, AGENT_DATA_DIR=str(self._data / f'child-default-{index}')),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for index in range(4)]
        try:
            deadline = time.monotonic() + 15
            while len(list(self._data.glob('go.*.ready'))) < 4 and time.monotonic() < deadline:
                if any(process.poll() is not None for process in processes):
                    break
                time.sleep(.01)
            gate.touch()
            results = []
            for process in processes:
                out, err = process.communicate(timeout=35)
                self.assertEqual(process.returncode, 0, err)
                results.append(json.loads(out))
            self.assertEqual(len({result['id'] for result in results}), 1)
            self.assertEqual(sum(result['created'] for result in results), 1)
            child_id = results[0]['id']
            claimed = self.store.claim_next_task('observer', lease_seconds=30)
            self.assertEqual(claimed['id'], child_id)
            self.assertIsNotNone(self.events.get_turn_by_task(child_id))
            notes = [event for event in self.events.list_events(self.conv['id']) if event['event_type'] == E.RECOVERY_NOTE]
            self.assertEqual(len(notes), 1)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_recovery_api_status_scope_and_authoritative_fields(self):
        task, turn = self.task()
        settings = replace(self.settings, users=[UserAccount(id='local', token='test-token'),
                                                UserAccount(id='other', token='other-token')])
        with TestClient(create_app(settings=settings, task_store=self.store,
                                  user_store=UserStore(self._data / 'users.db'))) as client, \
             patch.object(jobs, 'start_worker'):
            headers = {'Authorization': 'Bearer test-token'}
            # Startup must not accidentally schedule a recovery.
            self.assertEqual(len(self.store.list_tasks('local')), 1)
            detail = client.get(f"/api/jobs/{task['id']}", headers=headers).json()['job']
            self.assertTrue(detail['can_recover'])
            other = client.post(f"/api/jobs/{task['id']}/recover", headers={'Authorization': 'Bearer other-token'})
            self.assertEqual(other.status_code, 404, other.text)
            first = client.post(f"/api/jobs/{task['id']}/recover", headers=headers)
            self.assertEqual(first.status_code, 201, first.text)
            child = first.json()['job']
            self.assertEqual(child['conversation_id'], self.conv['id'])
            self.assertEqual(child['project_id'], self.project)
            self.store.update_task(child['id'], status='paused')
            again = client.post(f"/api/jobs/{task['id']}/recover", headers=headers)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertEqual(again.json()['job']['status'], 'paused')
            detail = client.get(f"/api/jobs/{task['id']}", headers=headers).json()['job']
            self.assertEqual((detail['can_recover'], detail['recovery_job_id']), (False, child['id']))
            listed = client.get('/api/jobs', params={'conversation_id': self.conv['id']}, headers=headers).json()['jobs']
            listed_source = next(job for job in listed if job['id'] == task['id'])
            self.assertEqual((listed_source['can_recover'], listed_source['recovery_job_id']), (False, child['id']))
            invalid = client.post(f"/api/jobs/{child['id']}/recover", headers=headers)
            self.assertEqual(invalid.status_code, 409, invalid.text)

    def test_steer_consumption_is_atomic_and_rebuilds_user_context(self):
        task, turn = self.task(status='running', turn_status='running')
        msg = self.store.add_task_message(task['id'], 'steer-one', 'steer', {'text': '请保留注释'})
        with patch.object(self.events, 'append_event_idempotent', side_effect=RuntimeError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'disk full'):
                self.events.consume_steers(task['id'], turn['id'], 'local')
        self.assertEqual(len(self.store.get_pending_messages(task['id'], types=['steer'])), 1)
        self.assertEqual(self.events.consume_steers(task['id'], turn['id'], 'local'), ['请保留注释'])
        self.assertEqual(self.events.consume_steers(task['id'], turn['id'], 'local'), [])
        events = self.events.list_events(self.conv['id'])
        steers = [e for e in events if e['payload'].get('task_message_id') == msg['id']]
        self.assertEqual(len(steers), 1)
        self.assertEqual((steers[0]['event_type'], steers[0]['role']), (E.USER_MESSAGE, 'user'))
        self.assertEqual(self.events.project_legacy_turns(self.conv['id'])[0]['user'], task['prompt'])
        for provider in ('openai', 'anthropic'):
            messages = build_provider_messages(events, provider, current_user_prompt=task['prompt'], current_turn_id=turn['id'])
            user_text = json.dumps([m for m in messages if m['role'] == 'user'], ensure_ascii=False)
            self.assertEqual(user_text.count('请保留注释'), 1)
            self.assertEqual(user_text.count(task['prompt']), 1)

    def test_steer_alone_cannot_replace_a_new_turn_prompt(self):
        task, turn = self.task(status='running', turn_status='running')
        only_steer = [{"id": "s1", "turn_id": turn['id'], "seq": 1,
                       "event_type": E.USER_MESSAGE, "payload": {
                           "content": "extra context", "source": "task_message"}}]
        for provider in ('openai', 'anthropic'):
            messages = build_provider_messages(only_steer, provider,
                current_user_prompt=task['prompt'], current_turn_id=turn['id'])
            text = json.dumps(messages, ensure_ascii=False)
            self.assertIn('extra context', text)
            self.assertIn(task['prompt'], text)

    def test_missing_original_checkpoint_is_reported_unavailable(self):
        task, turn = self.task(status='queued', turn_status='queued')
        def pause(*args, **kwargs):
            raise PauseRequested('pause')
        with self.assertRaises(PauseRequested):
            self.run_job(task, turn, pause)
        with self.store._connect() as conn:
            conn.execute("DELETE FROM checkpoints WHERE id=?", (f"before:{turn['id']}",))
        self.run_job(task, turn, lambda *args, **kwargs: 'done')
        result = self.store.get_task(task['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('初始快照', result['error_message'])
        terminal = [e for e in self.events.list_turn_events(turn['id']) if e['event_type'] == E.TURN_FAILED][-1]
        self.assertEqual(terminal['payload']['diff_status'], 'unavailable')

    def test_repeated_pause_preserves_real_edits_usage_and_final_diff(self):
        task, turn = self.task(status='queued', turn_status='queued')
        paths = [f'app/src/test/{name}.txt' for name in ('Before', 'Between', 'After')]
        repo = WorkspaceRepository('local', self.project, task_store=self.store)
        for index, path in enumerate(paths):
            def agent(*args, _index=index, _path=path, **kwargs):
                target = self.workspace / _path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('a') as handle:
                    handle.write('one execution\n')
                kwargs['on_event'](E.TOOL_RESULT, {'tool_call_id': f'write-{_index}', 'name': 'write_file', 'ok': True, 'input': {'path': _path}})
                usage = {'message_id': f'm-{_index}', 'usage': {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}}
                kwargs['on_event'](E.USAGE, usage)
                kwargs['on_event'](E.USAGE, usage)  # duplicate delivery must not count twice
                if _index < 2:
                    raise PauseRequested('test pause')
                return '文件已经修改完成'
            if index < 2:
                with self.assertRaises(PauseRequested):
                    self.run_job(task, turn, agent)
                self.assertFalse(any(cp['kind'] == 'after_turn' for cp in repo.list_checkpoints()))
                self.assertFalse(any(e['event_type'] == E.CHANGES for e in self.events.list_turn_events(turn['id'])))
            else:
                self.run_job(task, turn, agent)
        completed = self.store.get_task(task['id'])
        self.assertEqual(completed['status'], 'succeeded', completed['error_message'])
        self.assertEqual(completed['total_tokens'], 36)
        self.assertEqual({f['path'] for f in completed['changed_files']}, set(paths))
        self.assertEqual({f['path'] for f in repo.turn_diff(turn['id'])['files']}, set(paths))
        self.assertNotIn('系统校验', completed['final_message'])
        for path in paths:
            self.assertEqual((self.workspace / path).read_text(), 'one execution\n')
        events = self.events.list_turn_events(turn['id'])
        self.assertEqual(sum(e['event_type'] == E.CHANGES for e in events), 1)
        self.assertEqual(len([cp for cp in repo.list_checkpoints() if cp['kind'] == 'after_turn']), 1)
        # Immutable final snapshots continue to show the completed state.
        (self.workspace / paths[0]).write_text('unrelated later edit')
        self.assertIn('one execution', repo.turn_diff(turn['id'])['diff'])
        self.assertNotIn('unrelated later edit', repo.turn_diff(turn['id'])['diff'])

    def test_paused_build_keeps_task_artifact_and_rejects_changed_sources(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                task, turn = self.task(task_id=f'build-{changed}', status='queued', turn_status='queued')
                def build(*args, **kwargs):
                    from agent.workspace_inputs import capture_workspace_inputs
                    inputs = capture_workspace_inputs(self.workspace)
                    apk = latest_apk_path('local', self.project)
                    apk.parent.mkdir(parents=True, exist_ok=True)
                    apk.write_bytes(b'our build')
                    log = user_builds_dir('local') / self.project / f"{task['id']}.log"
                    log.write_text('BUILD SUCCESSFUL')
                    kwargs['on_event'](E.TOOL_RESULT, {'tool_call_id': task['id'] + '-build', 'name': 'run_gradle', 'ok': True,
                        'input': {'task': 'assembleDebug'}, 'model_output': f'BUILD SUCCESSFUL\n日志: {log}',
                        'summary': {'verification_receipt': {'schema_version': 1, 'task': 'assembleDebug',
                            'run_id': task['id'] + '-run', 'state': 'passed', 'evidence_time': time.time(),
                            'inputs_before': inputs, 'inputs_after': inputs}}})
                    raise PauseRequested('pause after build')
                with self.assertRaises(PauseRequested):
                    self.run_job(task, turn, build)
                latest_apk_path('local', self.project).write_bytes(b'another task build')
                unrelated = user_builds_dir('local') / self.project / 'other-task.log'
                unrelated.write_text('OTHER TASK LOG')
                if changed:
                    source = self.workspace / 'app/src/test/External.txt'
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.write_text('changed during pause')
                self.run_job(task, turn, lambda *args, **kwargs: '构建成功')
                result = self.store.get_task(task['id'])
                if changed:
                    self.assertEqual(result['status'], 'failed')
                    self.assertIsNone(result['apk_path'])
                    self.assertIn('重新构建', result['error_message'])
                else:
                    self.assertEqual(result['status'], 'succeeded', result['error_message'])
                    self.assertEqual(Path(result['apk_path']).read_bytes(), b'our build')
                    self.assertIn('BUILD SUCCESSFUL', Path(result['build_log_path']).read_text())
                    self.assertNotIn('OTHER TASK', Path(result['build_log_path']).read_text())

    def test_latest_receipt_survives_optional_capture_error(self):
        from agent.verification import verification_for_job
        from agent.workspace_inputs import capture_workspace_inputs
        for next_ok, expected in ((False, 'failed'), (True, 'unknown')):
            with self.subTest(expected=expected):
                task, turn = self.task(task_id=f'capture-{expected}', status='queued', turn_status='queued')
                inputs = capture_workspace_inputs(self.workspace)
                def agent(*args, **kwargs):
                    kwargs['on_event'](E.TOOL_RESULT, {'tool_call_id': task['id'] + '-first', 'name': 'run_gradle',
                        'ok': True, 'input': {'task': 'assembleDebug'}, 'summary': {'verification_receipt': {
                            'schema_version': 1, 'task': 'assembleDebug', 'run_id': 'old-pass', 'state': 'passed',
                            'evidence_time': time.time(), 'inputs_before': inputs, 'inputs_after': inputs}}})
                    # The next attempt has no receipt-rich summary. It must
                    # still replace the old successful validation fact.
                    kwargs['on_event'](E.TOOL_RESULT, {'tool_call_id': task['id'] + '-next', 'name': 'run_gradle',
                        'ok': next_ok, 'input': {'task': 'assembleDebug'}, 'error_type': None if next_ok else 'NonZeroExitCode'})
                    return 'finished'
                with patch.object(jobs, 'capture_gradle_result', side_effect=OSError('report volume unavailable')):
                    self.run_job(task, turn, agent)
                result = self.store.get_task(task['id'])
                runs = result['context']['feedback_runs']
                self.assertEqual(len(runs), 2)
                step = verification_for_job(result)['build']
                self.assertEqual(step['state'], expected)
                self.assertEqual(step['run_id'], task['id'] + '-next')

    def test_evicted_receipt_replay_cannot_replace_latest_execution(self):
        from agent.verification import verification_for_job
        from agent.workspace_inputs import capture_workspace_inputs
        task, turn = self.task(task_id='receipt-replay', status='queued', turn_status='queued')
        inputs = capture_workspace_inputs(self.workspace)
        def agent(*args, **kwargs):
            first = None
            for index in range(31):
                payload = {'tool_call_id': f'call-{index}', 'name': 'run_gradle', 'ok': index > 0,
                    'input': {'task': 'assembleDebug'}, 'summary': {'verification_receipt': {
                        'schema_version': 1, 'task': 'assembleDebug', 'run_id': f'run-{index}',
                        'state': 'passed' if index > 0 else 'failed', 'evidence_time': time.time(),
                        'inputs_before': inputs, 'inputs_after': inputs}}}
                if first is None:
                    first = payload
                kwargs['on_event'](E.TOOL_RESULT, payload)
            kwargs['on_event'](E.TOOL_RESULT, first)
            return 'finished'
        with patch.object(jobs, 'capture_gradle_result'):
            self.run_job(task, turn, agent)
        result = self.store.get_task(task['id'])
        self.assertEqual(len(result['context']['feedback_runs']), 30)
        self.assertEqual(verification_for_job(result)['build']['run_id'], 'run-30')
