import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agentes.setup import select_release, node_major, activate, prepare_antigravity_sandbox

class SetupTests(unittest.TestCase):
    def test_lts_selection_rejects_old_and_non_lts(self):
        releases=[{'version':v,'lts':lts,'files':['linux-x64']} for v,lts in
                  [('v18.1.0','old'),('v24.2.0','lts'),('v26.0.0',False),('v22.1.0','lts')]]
        self.assertEqual(select_release(releases,'linux','x64'),'v24.2.0')
        with self.assertRaises(RuntimeError): select_release(releases,'linux','arm64')

    def test_activate_idempotent_and_local(self):
        with patch.dict(os.environ,{'PATH':'/usr/bin'},clear=False):
            activate(); first=os.environ['PATH']; activate()
            self.assertEqual(first,os.environ['PATH'])
            self.assertIn('agentessemapi/tools/node/bin',first)

    def test_old_node_detected(self):
        with patch('agentes.setup.shutil.which',return_value='/usr/bin/node'), patch('agentes.setup.subprocess.run') as run:
            run.return_value.stdout='v18.19.1\n'
            self.assertEqual(node_major(),18)

    def test_antigravity_sandbox_settings_preserve_preferences(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'settings.json'
            path.write_text(json.dumps({'model':'gemini-x','trustedWorkspaces':['/tmp/x']}))
            workspace = Path(root)/'runs/run-1/work/agente/1'
            self.assertTrue(prepare_antigravity_sandbox(path, workspace))
            data = json.loads(path.read_text())
            self.assertEqual(data['model'], 'gemini-x')
            self.assertEqual(data['trustedWorkspaces'], ['/tmp/x'])
            self.assertTrue(data['enableTerminalSandbox'])
            self.assertEqual(data['toolPermission'], 'proceed-in-sandbox')
            self.assertIn(f'read_file({Path(root)/"runs/run-1"})', data['permissions']['allow'])
            self.assertIn(f'write_file({Path(root)/"runs/run-1"})', data['permissions']['allow'])
            self.assertFalse(prepare_antigravity_sandbox(path, workspace))
