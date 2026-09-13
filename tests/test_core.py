import asyncio
import copy
import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentes.cli import demo_plan
from agentes.engine import create_run, run_engine, check_outputs
from agentes.manifest import validate, ManifestError
from agentes.monitor import render, clean
from agentes.runners import execute, normalize, RunnerError, command
from agentes.storage import read_json, run_lock


class ManifestTests(unittest.TestCase):
    def test_cycle(self):
        p = demo_plan()
        p['tasks'][0]['depends_on'] = ['revisor']
        with self.assertRaises(ManifestError): validate(p)

    def test_missing_dependency(self):
        p = demo_plan()
        p['tasks'][0]['depends_on'] = ['missing']
        with self.assertRaises(ManifestError): validate(p)

    def test_unsafe_paths(self):
        for name in ('../secret', '/tmp/secret', 'a/../secret', 'a//b', 'inputs/a', 'x\\y', 'a\x1b'):
            with self.subTest(name=name):
                p = demo_plan()
                p['tasks'][0]['outputs'] = [name]
                with self.assertRaises(ManifestError): validate(p)

    def test_invalid_types(self):
        for key, value in [('timeout', True), ('retries', -1), ('runner', 'api'), ('depends_on', 'x')]:
            p = demo_plan(); p['tasks'][0][key] = value
            with self.assertRaises(ManifestError): validate(p)

    def test_unknown_and_duplicate(self):
        p = demo_plan(); p['tasks'][0]['command'] = 'rm -rf x'
        with self.assertRaises(ManifestError): validate(p)
        p = demo_plan(); p['tasks'][1]['id'] = p['tasks'][0]['id']
        with self.assertRaises(ManifestError): validate(p)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = create_run(demo_plan(), self.tmp.name)

    async def test_demo_dependencies_artifacts_and_resume(self):
        self.assertEqual(await run_engine(self.run, demo_delay=0), 'succeeded')
        state = read_json(self.run / 'state.json')
        review = state['tasks']['revisor']
        self.assertGreaterEqual(review['started_at'], state['tasks']['backend']['finished_at'])
        self.assertTrue((Path(review['workspace'])/'inputs/backend/soma.py').exists())
        await run_engine(self.run, demo_delay=0)
        self.assertEqual(read_json(self.run/'state.json')['tasks']['backend']['attempts'], 1)
        self.assertIn('3/3', render(self.run))
        self.assertIn('backend', render(self.run, 'backend'))

    async def test_user_inputs_are_copied_to_each_agent(self):
        source = Path(self.tmp.name) / 'relatorio.txt'
        source.write_text('dados do usuário')
        run = create_run(demo_plan(), self.tmp.name, [source])
        self.assertEqual(read_json(run/'state.json')['user_inputs'], ['relatorio.txt'])
        self.assertEqual(await run_engine(run, demo_delay=0), 'succeeded')
        workspace = Path(read_json(run/'state.json')['tasks']['backend']['workspace'])
        self.assertEqual((workspace/'inputs/usuario/relatorio.txt').read_text(), 'dados do usuário')

    async def test_failed_dependency_blocks_review(self):
        async def fail(task, workspace, *args):
            if task['id'] == 'backend': raise RunnerError('deliberate failure')
            for name in task['outputs']: (workspace/name).write_text('ok')
        with patch('agentes.engine.execute', fail):
            self.assertEqual(await run_engine(self.run), 'failed')
        state = read_json(self.run/'state.json')
        self.assertEqual(state['tasks']['revisor']['status'], 'blocked')
        self.assertEqual(state['tasks']['documentacao']['status'], 'succeeded')
        self.assertEqual(await run_engine(self.run, demo_delay=0), 'succeeded')
        self.assertEqual(read_json(self.run/'state.json')['tasks']['documentacao']['attempts'], 1)

    async def test_quota_pauses_without_retry(self):
        async def fail(*args): raise RunnerError('quota exceeded', 'quota')
        with patch('agentes.engine.execute', fail):
            status = await run_engine(self.run, {'total':1,'codex':1,'gemini':1,'demo':1})
        self.assertEqual(status, 'paused')
        state = read_json(self.run/'state.json')
        self.assertEqual(state['tasks']['backend']['attempts'], 1)
        self.assertEqual(state['tasks']['documentacao']['attempts'], 0)

    async def test_retry_uses_fresh_folder_and_error(self):
        p=demo_plan(); p['tasks']=p['tasks'][:1]; p['tasks'][0]['retries']=1
        run=create_run(p,self.tmp.name)
        prompts=[]; folders=[]
        async def fake(task, workspace, prompt, *args):
            folders.append(workspace); prompts.append(prompt)
            if len(prompts)==1: raise RunnerError('Corrija a sintaxe')
            (workspace/'soma.py').write_text('x = 1')
        with patch('agentes.engine.execute', fake):
            self.assertEqual(await run_engine(run), 'succeeded')
        self.assertNotEqual(folders[0], folders[1])
        self.assertIn('Corrija a sintaxe', prompts[1])

    async def test_cancel_is_recoverable(self):
        future=asyncio.create_task(run_engine(self.run,demo_delay=10))
        await asyncio.sleep(.05)
        future.cancel()
        with self.assertRaises(asyncio.CancelledError): await future
        self.assertEqual(read_json(self.run/'state.json')['status'],'interrupted')
        self.assertEqual(await run_engine(self.run,demo_delay=0),'succeeded')

    async def test_artifact_tamper_rejected(self):
        await run_engine(self.run,demo_delay=0)
        (self.run/'artifacts/backend/soma.py').write_text('tampered')
        with self.assertRaises(ValueError): await run_engine(self.run,demo_delay=0)

    async def test_changed_plan_rejected(self):
        plan = read_json(self.run/'plan.json')
        plan['tasks'][0]['prompt'] = 'instrução modificada'
        (self.run/'plan.json').write_text(json.dumps(plan))
        with self.assertRaises(ValueError): await run_engine(self.run, demo_delay=0)

    async def test_provider_concurrency_limit(self):
        plan = demo_plan()
        plan['tasks'][2]['depends_on'] = []
        plan['tasks'][2]['outputs'] = ['out.md']
        plan['tasks'][2]['checks'] = []
        run = create_run(plan, self.tmp.name)
        active = 0
        maximum = 0
        async def fake(task, workspace, *args):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(.02)
            for name in task['outputs']:
                (workspace/name).write_text('x = 1' if name.endswith('.py') else 'ok')
            active -= 1
        with patch('agentes.engine.execute', fake):
            await run_engine(run, {'total':3,'codex':1,'gemini':1,'demo':1})
        self.assertEqual(maximum, 1)

    def test_lock(self):
        with run_lock(self.run):
            with self.assertRaises(RuntimeError):
                with run_lock(self.run): pass

    def test_symlink_output_rejected(self):
        root=Path(self.tmp.name)
        (root/'outside').write_text('x')
        (root/'soma.py').symlink_to(root/'outside')
        with self.assertRaises(ValueError): check_outputs(demo_plan()['tasks'][0],root)

    def test_review_rejection_is_failure(self):
        root=Path(self.tmp.name)
        (root/'review.json').write_text('{"approved": false}')
        task={'outputs':['review.json'],'checks':[{'kind':'json_equals','path':'review.json','field':'approved','value':True}]}
        with self.assertRaises(ValueError): check_outputs(task,root)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def test_actions_and_reasoning_filter(self):
        self.assertEqual(normalize({'type':'tool_use','tool_name':'write_file'})[0], 'action')
        self.assertIsNone(normalize({'type':'item.completed','item':{'type':'reasoning','text':'private'}}))
        self.assertNotIn('\x1b',clean('\x1b[31mhello'))

    def test_subscription_command(self):
        cmd=command({'runner':'codex'},Path('/tmp/test'))
        self.assertIn('--sandbox',cmd)
        self.assertIn('forced_login_method="chatgpt"',cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',cmd)

        google = command({'runner':'gemini','timeout':600,'model':'gemini-teste'}, Path('/tmp/test'), 'atividade')
        self.assertEqual(google[0], 'agy')
        self.assertIn('--output-format', google)
        self.assertIn('stream-json', google)
        self.assertIn('--sandbox', google)
        self.assertIn('--add-dir', google)
        self.assertEqual(google[google.index('--add-dir') + 1], '/tmp/test')
        self.assertNotIn('--dangerously-skip-permissions', google)
        self.assertIn('--model', google)
        self.assertNotIn('-m', google)
        self.assertEqual(google[google.index('--model') + 1], 'gemini-teste')
        self.assertEqual(google[-2:], ['-p', 'atividade'])

    def test_antigravity_events(self):
        action = normalize({'event':'step_update','step_update':{
            'step_type':'tool','tool_name':'write_to_file','tool_info':{'parameters':{'path':'a.md'}}}})
        self.assertEqual(action[0], 'action')
        message = normalize({'event':'result','result':{'status':'SUCCESS','response':'pronto'}})
        self.assertEqual(message, ('message','pronto'))
        error = normalize({'event':'result','result':{'status':'ERROR','error':'falhou'}})
        self.assertEqual(error, ('error','falhou'))
        denied = normalize({'event':'result','result':{'status':'SUCCESS','response':'',
                           'denied_actions':[{'action':'command','display_name':'RunCommand'}]}})
        self.assertEqual(denied, ('error','Antigravity bloqueou uma permissão no modo headless: RunCommand'))

    async def test_api_environment_rejected(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fake'}):
            with self.assertRaises(RunnerError):
                await execute({'runner':'codex'},Path('/tmp'),'','/tmp/unused',lambda *x: None)

    async def test_subprocess_events_and_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); events=[]
            code='import sys,json; sys.stdin.read(); print(json.dumps({"type":"message","role":"assistant","content":"Olá"})); print("diagnostic", file=sys.stderr)'
            with patch('agentes.runners.command',return_value=[sys.executable,'-c',code]), patch.dict(os.environ,{},clear=True):
                await execute({'runner':'codex','timeout':5},root,'prompt',root/'raw.jsonl',lambda *e: events.append(e))
            self.assertIn(('message','Olá'),events)
            self.assertIn('diagnostic',(root/'raw.jsonl').read_text())

    async def test_timeout_terminates_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            code=('import subprocess,sys,time; '
                  'p=subprocess.Popen([sys.executable,"-c","import time,pathlib; time.sleep(2); pathlib.Path(\'escaped\').write_text(\'bad\')"]); '
                  'time.sleep(20)')
            with patch('agentes.runners.command',return_value=[sys.executable,'-c',code]), patch.dict(os.environ,{},clear=True):
                with self.assertRaises(RunnerError) as ctx:
                    await execute({'runner':'codex','timeout':.1},root,'',root/'raw',lambda *e:None)
                self.assertEqual(ctx.exception.category,'timeout')
            await asyncio.sleep(2.1)
            self.assertFalse((root/'escaped').exists())


if __name__ == '__main__': unittest.main()
