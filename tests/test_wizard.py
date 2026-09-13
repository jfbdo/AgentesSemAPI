import tempfile
import unittest
import subprocess
from unittest.mock import patch
from pathlib import Path
from agentes.wizard import demo_team, team_contract, agent_count, start, planner_model, edit_models, choose_inputs
from agentes.cli import demo_plan

class WizardTests(unittest.TestCase):
    def test_exact_demo_count(self):
        for n in (1, 3, 8):
            plan = demo_team('atividade', n)
            self.assertEqual(len(plan['tasks']), n)
            self.assertEqual(len(plan['tasks'][-1]['depends_on']), n-1)

    def test_real_contract(self):
        p = demo_plan()
        for t in p['tasks']: t['runner'] = 'codex'
        empty = {'codex': [], 'gemini': []}
        with self.assertRaises(ValueError): team_contract(p,3,empty)
        p['tasks'][1]['runner'] = 'gemini'
        team_contract(p,3,empty)
        p['tasks'][0]['model'] = 'inventado'
        with self.assertRaises(ValueError):
            team_contract(p,3,{'codex':[{'id':'outro'}], 'gemini':[]})
        p['tasks'][2]['model'] = 'inventado'
        team_contract(p,3,{'codex':[{'id':'inventado'}], 'gemini':[]})
        with self.assertRaises(ValueError):
            team_contract(p,2,{'codex':[{'id':'inventado'}], 'gemini':[]})

    def test_all_available_models_are_explicit(self):
        p = demo_plan()
        p['tasks'][0]['runner'] = 'codex'; p['tasks'][0]['model'] = 'gpt-a'
        p['tasks'][1]['runner'] = 'gemini'; p['tasks'][1]['model'] = 'gemini-a'
        p['tasks'][2]['runner'] = 'codex'; p['tasks'][2]['model'] = 'gpt-a'
        catalogs = {'codex':[{'id':'gpt-a'}], 'gemini':[{'id':'gemini-a'}]}
        self.assertIs(team_contract(p, 3, catalogs), p)
        p['tasks'][1].pop('model')
        with self.assertRaises(ValueError): team_contract(p, 3, catalogs)

    def test_planner_model_prefers_capable_available_model(self):
        models = [{'id':'gpt-5.6-luna'}, {'id':'gpt-6-astra'}]
        self.assertEqual(planner_model(models), 'gpt-6-astra')

    def test_count_reprompts(self):
        with patch('builtins.input',side_effect=['abc','0','9','4']):
            self.assertEqual(agent_count(),4)

    def test_file_picker_combines_different_folders(self):
        picks = [
            subprocess.CompletedProcess([], 0, '/pasta-a/a.pdf\n/pasta-a/b.xlsx\n', ''),
            subprocess.CompletedProcess([], 0, '/pasta-b/c.docx\n', ''),
        ]
        with patch('builtins.input', side_effect=['s', 's', 'n']), \
             patch('agentes.wizard.shutil.which', return_value='/usr/bin/zenity'), \
             patch.dict('agentes.wizard.os.environ', {'DISPLAY': ':0'}), \
             patch('agentes.wizard.subprocess.run', side_effect=picks):
            paths = choose_inputs()
        self.assertEqual([path.name for path in paths], ['a.pdf', 'b.xlsx', 'c.docx'])

    def test_user_can_replace_models_from_provider_catalog(self):
        plan = demo_plan()
        plan['tasks'][1]['runner'] = 'gemini'
        plan['tasks'][1]['model'] = 'gemini-pro'
        catalogs = {
            'codex': [{'id':'gpt-a', 'description':'Codex A'}],
            'gemini': [
                {'id':'gemini-pro', 'description':'Pro'},
                {'id':'gemini-flash-high', 'description':'Flash rápido'},
            ],
        }
        with patch('builtins.input', side_effect=['2', '2', '']):
            self.assertIs(edit_models(plan, catalogs), plan)
        self.assertEqual(plan['tasks'][1]['model'], 'gemini-flash-high')

    def test_model_choice_can_change_provider(self):
        plan = demo_plan()
        plan['tasks'][0]['runner'] = 'codex'
        catalogs = {'codex':[{'id':'gpt-a'}], 'gemini':[{'id':'gemini-flash-high'}]}
        with patch('builtins.input', side_effect=['1', '1', '']):
            edit_models(plan, catalogs)
        self.assertEqual(plan['tasks'][0]['runner'], 'gemini')
        self.assertEqual(plan['tasks'][0]['model'], 'gemini-flash-high')

    def test_guided_demo_launch_without_paths(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('builtins.input',side_effect=['2','Criar exemplo','3','s','0']), \
                 patch('agentes.wizard.shutil.which',return_value='/usr/bin/tmux'), \
                 patch('agentes.cli.launch') as launch:
                start(Path(root))
            self.assertEqual(launch.call_count,1)
            run,args=launch.call_args.args
            self.assertTrue((run/'plan.json').exists())
            self.assertTrue(args.dashboard)
            self.assertEqual(args.workers,3)
            self.assertEqual(args.codex_workers,1)
            self.assertEqual(args.gemini_workers,1)
