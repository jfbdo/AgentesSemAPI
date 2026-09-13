import tempfile
import unittest
from agentes.cli import demo_plan
from agentes.engine import create_run
from agentes.monitor import wrap_lines, cells, render, artifacts_uri, comfortable_page_size, agent_pages
from agentes.storage import read_json, write_json

class MonitorTests(unittest.TestCase):
    def test_dashboard_pages_limit_agents_and_adapt_to_terminal(self):
        tasks = list(range(9))
        self.assertEqual(comfortable_page_size(180, 50), 4)
        self.assertEqual(comfortable_page_size(100, 30), 1)
        pages = agent_pages(tasks, 4)
        self.assertEqual([len(page) for page in pages], [4, 4, 1])

    def test_wrap_preserves_words_and_long_paths(self):
        text = '🎯 Uma tarefa com documentação ' + 'caminho/' * 20
        lines = wrap_lines([text], 24)
        self.assertTrue(all(cells(line) <= 24 for line in lines))
        self.assertEqual(''.join(lines).replace(' ', ''), text.replace(' ', ''))

    def test_small_panel_does_not_discard_content(self):
        with tempfile.TemporaryDirectory() as root:
            run = create_run(demo_plan(), root)
            view = render(run, width=24, height=5)
            self.assertIn('revisor', view)
            self.assertGreater(len(view.splitlines()), 5)
            self.assertTrue(all(cells(line) <= 24 for line in view.splitlines()))

    def test_coordinator_shows_models_completion_and_folder_link(self):
        with tempfile.TemporaryDirectory() as root:
            plan = demo_plan()
            plan['tasks'][0]['model'] = 'modelo-teste'
            run = create_run(plan, root)
            view = render(run, width=100)
            self.assertIn('modelo modelo-teste', view)
            self.assertIn('file://', artifacts_uri(run))

    def test_coordinator_final_banner_distinguishes_success_and_failure(self):
        with tempfile.TemporaryDirectory() as root:
            run = create_run(demo_plan(), root)
            state = read_json(run/'state.json')
            state['status'] = 'succeeded'
            for task in state['tasks'].values():
                task['status'] = 'succeeded'
            write_json(run/'state.json', state)
            succeeded = render(run, width=100)
            self.assertLess(succeeded.index('FINALIZADO SEM ERROS'), succeeded.index('Coordenador'))
            self.assertIn('[ 📂 ABRIR PASTA DE ENTREGAS ]', succeeded)
            state['status'] = 'failed'
            state['tasks']['backend']['status'] = 'failed'
            state['tasks']['revisor']['status'] = 'blocked'
            write_json(run/'state.json', state)
            failed = render(run, width=100)
            self.assertIn('FINALIZADO COM ERROS', failed)
            self.assertIn('1 falharam • 1 bloqueados', failed)

    def test_agent_view_uses_short_summary_instead_of_full_prompt(self):
        with tempfile.TemporaryDirectory() as root:
            plan = demo_plan()
            plan['tasks'][0]['summary'] = 'Resumo curto e claro.'
            plan['tasks'][0]['prompt'] = 'DETALHE_TECNICO_' * 100
            run = create_run(plan, root)
            view = render(run, 'backend', width=100)
            self.assertIn('Resumo curto e claro.', view)
            self.assertNotIn('DETALHE_TECNICO_', view)
