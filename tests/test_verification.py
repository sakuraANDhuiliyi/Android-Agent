from __future__ import annotations

import time
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent import jobs, tools
from agent.api import create_app
from agent.conversation_events import ConversationEventStore
from agent.feedback import capture_gradle_result, fresh_test_reports, snapshot_test_reports, persist_gradle_receipt
from agent.project import init_project
from agent.users import UserStore
from agent.verification import verification_for_job
from agent.workspace_inputs import capture_workspace_inputs
from test_workspace import IsolatedWorkspaceMixin, _api_settings


class VerificationReceiptTests(IsolatedWorkspaceMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = self._store()
        self.project = init_project('verification', package='com.example.verification', user_id='local')
        self.workspace = self._workspaces / 'local' / self.project
        self.store.create_task(dict(id='job', user_id='local', project_id=self.project,
            prompt='verify', status='running', created_at=time.time(), context={}))
        self.reports = self.workspace / 'app/build/test-results/testDebugUnitTest'
        self.reports.mkdir(parents=True)

    def receipt(self, task='testDebugUnitTest', **overrides):
        inputs = capture_workspace_inputs(self.workspace)
        return {'schema_version': 1, 'job_id': 'job', 'task': task, 'run_id': 'run-1',
                'evidence_time': time.time(), 'duration_ms': 20, 'state': 'passed',
                'inputs_before': inputs, 'inputs_after': inputs, 'report_state': 'complete',
                'counts': {'total': 2, 'passed': 1, 'failed': 0, 'skipped': 1}, **overrides}

    def store_receipt(self, receipt, *, final=True):
        job = self.store.get_task('job')
        context = job['context']
        runs = list(context.get('feedback_runs') or [])
        runs.append({'task': receipt['task'], 'verification_receipt': receipt, 'problems': []})
        context = {**context, 'feedback_runs': runs}
        if final:
            context['verification_final_inputs'] = capture_workspace_inputs(self.workspace)
        self.store.update_task('job', context=context, status='succeeded' if final else 'running')
        return verification_for_job(self.store.get_task('job'))

    def test_execution_matrix_retains_result_separate_from_input_relation(self):
        cases = [
            ({}, 'passed'),
            ({'counts': {'total': 0, 'passed': 0, 'failed': 0, 'skipped': 0}}, 'no_tests'),
            ({'counts': {'total': 2, 'passed': 0, 'failed': 0, 'skipped': 2}}, 'skipped'),
            ({'counts': {'total': 2, 'passed': 1, 'failed': 1, 'skipped': 0}}, 'failed'),
            ({'state': 'failed'}, 'failed'), ({'state': 'canceled'}, 'canceled'),
            ({'state': 'interrupted'}, 'interrupted'),
            ({'report_state': 'missing', 'counts': None}, 'unknown'),
            ({'counts': {'total': 2, 'passed': -1, 'failed': 1, 'skipped': 2}}, 'unknown'),
            ({'counts': {'total': 3, 'passed': 1, 'failed': 0, 'skipped': 1}}, 'unknown'),
            ({'counts': {'total': True, 'passed': True, 'failed': 0, 'skipped': 0}}, 'unknown'),
            ({'job_id': 'other'}, 'unknown'), ({'run_id': None}, 'unknown'),
            ({'evidence_time': float('nan')}, 'unknown'),
        ]
        for overrides, expected in cases:
            with self.subTest(overrides=overrides):
                step = self.store_receipt(self.receipt(**overrides))['unit_tests']
                self.assertEqual(step['state'], expected)
        before = capture_workspace_inputs(self.workspace)
        (self.workspace / 'settings.gradle.kts').write_text('// changed root input')
        step = self.store_receipt(self.receipt(inputs_before=before, inputs_after=before))['unit_tests']
        self.assertEqual((step['state'], step['source_match']), ('passed', 'changed'))
        step = self.store_receipt(self.receipt(inputs_before={'status': 'unknown'}))['unit_tests']
        self.assertEqual((step['state'], step['source_match']), ('passed', 'unknown'))

    def test_last_run_per_kind_same_job_historical_snapshot_and_legacy_unknown(self):
        self.store_receipt(self.receipt())
        dto = self.store_receipt(self.receipt('assembleDebug', run_id='build'))
        self.assertEqual(dto['unit_tests']['state'], 'passed')
        self.assertEqual(dto['build']['state'], 'passed')
        self.assertEqual(dto['installation']['state'], 'unknown')
        (self.workspace / 'settings.gradle.kts').write_text('// later independent edit')
        with patch('agent.workspace_inputs.capture_workspace_inputs', side_effect=AssertionError('DTO must not scan')):
            self.assertEqual(verification_for_job(self.store.get_task('job')), dto)
        self.assertEqual(self.store_receipt(self.receipt(state='canceled'))['unit_tests']['state'], 'canceled')
        old = verification_for_job({'id': 'old', 'context': {'feedback_runs': [{'task': 'testDebugUnitTest', 'status': 'success', 'tests': {'reported': True, 'passed': 20}}]}})
        self.assertEqual(old['unit_tests']['state'], 'unknown')
        fresh = verification_for_job({'id': 'recovery', 'context': {}})
        self.assertEqual(fresh['unit_tests']['state'], 'not_run')

    def test_junit_scope_freshness_and_semantic_validation(self):
        current = self.reports / 'TEST-current.xml'
        current.write_text('<testsuite><testcase name="old"/></testsuite>')
        variant = self.workspace / 'lib/build/test-results/testReleaseUnitTest'
        variant.mkdir(parents=True)
        variant.joinpath('TEST-wrong.xml').write_text('<testsuite><testcase/></testsuite>')
        baseline = snapshot_test_reports(self.workspace)
        self.assertEqual(fresh_test_reports(self.workspace, baseline)[0]['report_state'], 'missing')
        for body, expected in [
            ('<testsuite tests="2" failures="0" errors="0" skipped="1"><testcase/><testcase><skipped/></testcase></testsuite>', 'complete'),
            ('<testsuite tests="0"/>', 'complete'),
            ('<testsuites tests="1"><testsuite tests="1"><testcase/></testsuite></testsuites>', 'complete'),
            ('<testsuite tests="10" failures="1"><testcase/></testsuite>', 'invalid'),
            ('<testsuite tests="-1"/>', 'invalid'),
            ('<testsuite failures="1"/>', 'invalid'),
            ('<testsuite><testcase><failure/><skipped/></testcase></testsuite>', 'invalid'),
            ('<testsuite><testsuite><testcase/></testsuite></testsuite>', 'invalid'),
            ('<html/>', 'invalid'), ('<testsuite>', 'invalid'),
            ('<!DOCTYPE testsuite><testsuite/>', 'invalid'),
        ]:
            with self.subTest(body=body):
                baseline = snapshot_test_reports(self.workspace)
                current.write_text(body)
                report, _ = fresh_test_reports(self.workspace, baseline)
                self.assertEqual(report['report_state'], expected)
        baseline = snapshot_test_reports(self.workspace)
        current.write_text('<testsuite><testcase/></testsuite>')
        (self.reports / 'TEST-invalid.xml').write_text('broken')
        self.assertEqual(fresh_test_reports(self.workspace, baseline)[0]['report_state'], 'invalid')

    def test_actual_sandboxed_gradle_receipt_and_persisted_report(self):
        # Execute a harmless local wrapper through the actual command runner.
        # Its reports contain one passed and one skipped test in this variant;
        # a release report and untouched old debug XML must never inflate it.
        self.reports.joinpath('TEST-old.xml').write_text('<testsuite><testcase/></testsuite>')
        wrapper = self.workspace / 'gradlew'
        wrapper.write_text('''#!/bin/sh
mkdir -p app/build/test-results/testDebugUnitTest lib/build/test-results/testReleaseUnitTest
printf '%s' '<testsuite tests="2" skipped="1"><testcase/><testcase><skipped/></testcase></testsuite>' > app/build/test-results/testDebugUnitTest/TEST-current.xml
printf '%s' '<testsuite><testcase/></testsuite>' > lib/build/test-results/testReleaseUnitTest/TEST-other.xml
echo 'BUILD SUCCESSFUL'
''')
        wrapper.chmod(0o755)
        with patch('agent.tools.ensure_local_properties', return_value=str(self.workspace)):
            result = tools.run_gradle(self.workspace, 'local', self.project, 'testDebugUnitTest')
        self.assertTrue(result.ok, result.output)
        receipt = result.summary['verification_receipt']
        self.assertEqual(receipt['counts'], {'total': 2, 'passed': 1, 'failed': 0, 'skipped': 1})
        self.assertEqual(receipt['inputs_before']['digest'], receipt['inputs_after']['digest'])
        # Persistence uses the already-captured invocation, even if disk reports
        # have subsequently been replaced by a different task.
        self.reports.joinpath('TEST-current.xml').write_text('<testsuite><testcase><failure/></testcase></testsuite>')
        capture_gradle_result(self.store, 'local', self.project, 'job', {'name': 'run_gradle', 'input': {'task': 'testDebugUnitTest'},
            'ok': result.ok, 'tool_call_id': 'call', 'summary': result.summary, 'model_output': result.output}, time.time() - 5)
        step = verification_for_job(self.store.get_task('job'))['unit_tests']
        self.assertEqual(step['state'], 'passed')
        self.assertEqual(step['counts']['failed'], 0)

    def test_actual_command_cancel_retains_partial_counts_without_pass(self):
        wrapper = self.workspace / 'gradlew'
        wrapper.write_text('''#!/bin/sh
mkdir -p app/build/test-results/testDebugUnitTest
printf '%s' '<testsuite><testcase/></testsuite>' > app/build/test-results/testDebugUnitTest/TEST-partial.xml
sleep 10
echo 'BUILD SUCCESSFUL'
''')
        wrapper.chmod(0o755)
        started = time.monotonic()
        def cancel():
            if time.monotonic() - started > 0.7:
                from agent.processes import CancellationRequested
                raise CancellationRequested('test cancellation')
        with patch('agent.tools.ensure_local_properties', return_value=str(self.workspace)):
            result = tools.run_gradle(self.workspace, 'local', self.project, 'testDebugUnitTest', cancel_check=cancel)
        self.assertFalse(result.ok)
        self.assertEqual(result.summary['verification_receipt']['state'], 'canceled')
        receipt = {**result.summary['verification_receipt'], 'job_id': 'job'}
        self.assertEqual(self.store_receipt(receipt)['unit_tests']['state'], 'canceled')

    def test_cancel_kill_before_token_watcher_is_classified_as_canceled(self):
        from agent.processes import ProcessResult, CancellationRequested
        import threading
        (self.workspace / 'gradlew').write_text('#!/bin/sh\nexit 0\n')
        # No watcher gets a chance to mark the token. The durable cancellation
        # check must override the process's plain negative signal exit.
        def check():
            if threading.current_thread() is threading.main_thread():
                raise CancellationRequested('HTTP cancel already persisted')
        result = ProcessResult(False, -15, '', '', 1, False, None, 'NonZeroExitCode')
        with patch('agent.tools.ensure_local_properties', return_value=str(self.workspace)), patch('agent.tools._run_command', return_value=result):
            actual = tools.run_gradle(self.workspace, 'local', self.project, 'testDebugUnitTest', cancel_check=check)
        self.assertEqual(actual.error_type, 'CancellationRequested')
        self.assertEqual(actual.summary['verification_receipt']['state'], 'canceled')

    def test_crashed_pending_gradle_overrides_earlier_pass_and_is_idempotent(self):
        self.store_receipt(self.receipt(), final=False)
        conversation = self.store.create_conversation('local', self.project)
        self.store.update_task('job', conversation_id=conversation['id'])
        events = ConversationEventStore(self.store)
        turn = events.create_turn(conversation['id'], 'local', self.project, task_id='job', status='running')
        events.append_event_idempotent(conversation['id'], turn['id'], 'tool_call', 'call:lost',
            {'tool_call_id': 'lost', 'name': 'run_gradle', 'input': {'task': 'testDebugUnitTest'}}, task_id='job')
        original_append = ConversationEventStore.append_event_idempotent
        def fail_after_receipt(store, conversation_id, turn_id, event_type, *args, **kwargs):
            if event_type == 'tool_result':
                raise RuntimeError('simulate another crash before synthetic result')
            return original_append(store, conversation_id, turn_id, event_type, *args, **kwargs)
        with patch.object(ConversationEventStore, 'append_event_idempotent', autospec=True, side_effect=fail_after_receipt):
            with self.assertRaisesRegex(RuntimeError, 'another crash'):
                self.store.recover_interrupted()
        # Receipt precedes the synthetic result, so another recovery safely
        # retries the event and cannot expose the previous successful attempt.
        self.assertEqual(verification_for_job(self.store.get_task('job'))['unit_tests']['state'], 'interrupted')
        self.store.recover_interrupted()
        job = self.store.get_task('job')
        step = verification_for_job(job)['unit_tests']
        self.assertEqual((step['state'], step['source_match'], step['run_id']), ('interrupted', 'unknown', 'lost'))
        self.store.recover_interrupted()
        self.assertEqual(len(self.store.get_task('job')['context']['feedback_runs']), 2)

    def test_api_list_detail_feedback_same_receipt_and_empty_job_scope(self):
        dto = self.store_receipt(self.receipt())
        self.store.create_task(dict(id='empty', user_id='local', project_id=self.project,
            prompt='empty', status='succeeded', created_at=time.time(), context={}))
        client = TestClient(create_app(settings=_api_settings(), task_store=self.store, user_store=UserStore(self._data / 'users.db')),
                            headers={'Authorization': 'Bearer test-token'})
        self.addCleanup(client.close)
        with patch('agent.workspace_inputs.capture_workspace_inputs', side_effect=AssertionError('API read must not scan')):
            detail = client.get('/api/jobs/job').json()['job']
            rows = client.get('/api/jobs', params={'project_id': self.project}).json()['jobs']
            feedback = client.get(f'/api/projects/{self.project}/feedback', params={'job_id': 'job'}).json()
            self.assertEqual(detail['verification'], dto)
            self.assertEqual(next(j for j in rows if j['id'] == 'job')['verification'], dto)
            self.assertEqual(feedback['verification'], dto)
            self.assertEqual(feedback['job_status'], 'succeeded')
            empty = client.get(f'/api/projects/{self.project}/feedback', params={'job_id': 'empty'}).json()
            self.assertEqual(empty['job_id'], 'empty')
            self.assertEqual(empty['verification']['unit_tests']['state'], 'not_run')
            self.assertIsNone(empty['tests'])
            self.store.create_task(dict(id='foreign', user_id='other', project_id=self.project,
                prompt='other account', status='succeeded', created_at=time.time(), context={}))
            self.assertEqual(client.get('/api/jobs/foreign').status_code, 404)
            self.assertEqual(client.get(f'/api/projects/{self.project}/feedback', params={'job_id': 'foreign'}).status_code, 404)

    def test_recovery_repairs_completed_result_missing_its_receipt(self):
        self.store_receipt(self.receipt(), final=False)
        conversation = self.store.create_conversation('local', self.project)
        self.store.update_task('job', conversation_id=conversation['id'])
        events = ConversationEventStore(self.store)
        turn = events.create_turn(conversation['id'], 'local', self.project, task_id='job', status='running')
        events.append_event_idempotent(conversation['id'], turn['id'], 'tool_call', 'call:completed',
            {'tool_call_id': 'completed', 'name': 'run_gradle', 'input': {'task': 'testDebugUnitTest'}}, task_id='job')
        events.append_event_idempotent(conversation['id'], turn['id'], 'tool_result', 'result:completed',
            {'tool_call_id': 'completed', 'name': 'run_gradle', 'ok': False, 'error_type': 'NonZeroExitCode'}, task_id='job')
        self.store.recover_interrupted()
        job = self.store.get_task('job')
        step = verification_for_job(job)['unit_tests']
        self.assertEqual((step['state'], step['run_id']), ('failed', 'completed'))
        self.store.recover_interrupted()
        self.assertEqual(len(self.store.get_task('job')['context']['feedback_runs']), 2)

    def test_result_receipt_survives_crash_before_canonical_result(self):
        self.store_receipt(self.receipt(), final=False)
        conversation = self.store.create_conversation('local', self.project)
        self.store.update_task('job', conversation_id=conversation['id'])
        events = ConversationEventStore(self.store)
        turn = events.create_turn(conversation['id'], 'local', self.project, task_id='job', status='running')
        events.append_event_idempotent(conversation['id'], turn['id'], 'tool_call', 'call:receipt-first',
            {'tool_call_id': 'receipt-first', 'name': 'run_gradle', 'input': {'task': 'testDebugUnitTest'}}, task_id='job')
        persist_gradle_receipt(self.store, 'local', 'job', {'tool_call_id': 'receipt-first', 'ok': False,
            'input': {'task': 'testDebugUnitTest'}, 'summary': {'verification_receipt': self.receipt(state='failed', run_id='new-run')}})
        # Simulate a crash here: primary receipt committed but no result event.
        original = self.store.get_task('job')['context']['feedback_runs'][-1]
        self.store.recover_interrupted()
        self.store.recover_interrupted()
        recovered = self.store.get_task('job')
        self.assertEqual(len(recovered['context']['feedback_runs']), 2)
        self.assertEqual(recovered['context']['feedback_runs'][-1], original)
        self.assertEqual(verification_for_job(recovered)['unit_tests']['state'], 'failed')

    def test_primary_and_enriched_reports_redact_synthetic_secrets(self):
        secret = 'synthetic_receipt_password'
        receipt = self.receipt(test_problems=[{'message': f'password={secret}'}])
        payload = {'tool_call_id': 'redact-call', 'ok': True, 'input': {'task': 'testDebugUnitTest'},
                   'summary': {'verification_receipt': receipt}, 'model_output': f'BUILD SUCCESSFUL\nwarning: password={secret}'}
        persist_gradle_receipt(self.store, 'local', 'job', payload)
        with self.store._connect() as conn:
            raw = conn.execute("SELECT context_json FROM tasks WHERE id='job'").fetchone()[0]
        self.assertNotIn(secret, raw)
        capture_gradle_result(self.store, 'local', self.project, 'job', payload, 0)
        with self.store._connect() as conn:
            raw = conn.execute("SELECT context_json FROM tasks WHERE id='job'").fetchone()[0]
        self.assertNotIn(secret, raw)
        self.assertIn('[REDACTED]', raw)

    def test_provider_cancellation_preserves_gradle_task_receipt_binding(self):
        from agent.loop import CancellationRequested, _run_anthropic, _run_openai_compatible
        from test_conversation_integration import settings, openai_response, openai_tool_call, anthropic_response
        for provider in ('openai', 'anthropic'):
            with self.subTest(provider=provider):
                self.store.update_task('job', context={})
                self.store_receipt(self.receipt(), final=False)
                if provider == 'openai':
                    response = openai_response(tool_calls=[openai_tool_call('cancel-' + provider, 'run_gradle', '{"task":"testDebugUnitTest"}')], finish_reason='tool_calls')
                    fallback, run = 'agent.loop._chat_completion_with_fallback', _run_openai_compatible
                    module = SimpleNamespace(OpenAI=lambda **kwargs: object())
                else:
                    response = anthropic_response([SimpleNamespace(type='tool_use', id='cancel-' + provider, name='run_gradle', input={'task': 'testDebugUnitTest'})], stop_reason='tool_use', response_id='response')
                    fallback, run = 'agent.loop._anthropic_message_with_fallback', _run_anthropic
                    module = SimpleNamespace(Anthropic=lambda **kwargs: object())
                emitted = []
                with patch.dict(sys.modules, {provider: module}), patch(fallback, return_value=(response, 'fake-model')), \
                     patch('agent.loop._openai_tools', return_value=[]), patch('agent.loop.get_tool_definitions', return_value=[]), \
                     patch('agent.loop.dispatch_agent_tool', side_effect=CancellationRequested('cancel approval')):
                    with self.assertRaises(CancellationRequested):
                        run(settings(provider), self.workspace, 'local', self.project, 'test', 'system',
                            lambda kind, payload: emitted.append((kind, payload)), None, [], task_id='job', turn_id='turn')
                payload = next(payload for kind, payload in emitted if kind == 'tool_result')
                self.assertEqual(payload['input'], {'task': 'testDebugUnitTest'})
                persist_gradle_receipt(self.store, 'local', 'job', payload)
                dto = verification_for_job(self.store.get_task('job'))
                self.assertEqual(dto['unit_tests']['state'], 'canceled')
                self.assertEqual(dto['build']['state'], 'not_run')


if __name__ == '__main__':
    unittest.main()
