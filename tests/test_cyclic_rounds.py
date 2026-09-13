import tempfile
import unittest

from agentes.engine import create_run
from agentes.manifest import validate
from agentes.monitor import render
from agentes.storage import read_json, write_json
from agentes.wizard import detect_rounds, demo_team, team_contract


class CyclicRoundsTests(unittest.TestCase):
    def test_detect_rounds_parses_numbers_and_keywords(self):
        self.assertEqual(detect_rounds("Revisar skill megabrain em 3 rodadas de melhoria"), 3)
        self.assertEqual(detect_rounds("Fazer 2 ciclos de refinamento"), 2)
        self.assertEqual(detect_rounds("Refinar código em 4 iterações"), 4)
        self.assertEqual(detect_rounds("Texto com debate entre os agentes"), 3)
        self.assertEqual(detect_rounds("Artigo com revisão cruzada"), 3)
        # Objectives without rounds or with only 1 round
        self.assertIsNone(detect_rounds("Criar um script em Python simples"))
        self.assertIsNone(detect_rounds("Escrever documentação da API"))
        self.assertIsNone(detect_rounds("Apenas 1 rodada de teste"))

    def test_demo_team_cyclic_chain_links_rounds_sequentially(self):
        count = 2
        rounds = 3
        plan = demo_team("Revisar documento", count=count, total_rounds=rounds)
        self.assertEqual(len(plan["tasks"]), count * rounds)
        
        # Round 1
        t_r1_1 = plan["tasks"][0]
        t_r1_2 = plan["tasks"][1]
        self.assertEqual(t_r1_1["stage"], "Rodada 1")
        self.assertEqual(t_r1_1["depends_on"], [])
        self.assertEqual(t_r1_2["depends_on"], [t_r1_1["id"]])

        # Round 2: first task must depend on last task of round 1!
        t_r2_1 = plan["tasks"][2]
        t_r2_2 = plan["tasks"][3]
        self.assertEqual(t_r2_1["stage"], "Rodada 2")
        self.assertEqual(t_r2_1["depends_on"], [t_r1_2["id"]])
        self.assertEqual(t_r2_2["depends_on"], [t_r2_1["id"]])

        # Round 3: first task must depend on last task of round 2!
        t_r3_1 = plan["tasks"][4]
        t_r3_2 = plan["tasks"][5]
        self.assertEqual(t_r3_1["stage"], "Rodada 3")
        self.assertEqual(t_r3_1["depends_on"], [t_r2_2["id"]])
        self.assertEqual(t_r3_2["depends_on"], [t_r3_1["id"]])

        # Validate structure against engine manifest
        self.assertEqual(validate(plan)["version"], 2)

    def test_monitor_renders_cyclic_rounds_indicator(self):
        with tempfile.TemporaryDirectory() as root:
            plan = demo_team("Revisar skill em 3 ciclos", count=2, total_rounds=3)
            run = create_run(plan, root)
            write_json(run / "team-selection.json", {
                "coordinator": {"runner": "demo", "model": "demo-coordenador"},
                "requested_agents": 2,
                "total_rounds": 3
            })
            rendered = render(run, width=100)
            self.assertIn("3 rodadas cíclicas", rendered)

    def test_team_contract_validates_cyclic_task_count(self):
        catalogs = {
            "gemini": [{"id": "gemini-3.8-flash-high"}],
            "codex": [{"id": "gpt-5.6-sol"}]
        }
        plan = {
            "version": 2,
            "objective": "Pipeline cíclica teste",
            "tasks": [
                {
                    "id": "r1_a1", "role": "Criador", "stage": "Rodada 1", "runner": "codex",
                    "model": "gpt-5.6-sol", "summary": "Criar rascunho.",
                    "prompt": "Criar", "outputs": ["r1.md"], "depends_on": []
                },
                {
                    "id": "r1_a2", "role": "Revisor", "stage": "Rodada 1", "runner": "gemini",
                    "model": "gemini-3.8-flash-high", "summary": "Revisar rascunho.",
                    "prompt": "Revisar", "outputs": ["r1_rev.md"], "depends_on": ["r1_a1"]
                },
                {
                    "id": "r2_a1", "role": "Criador", "stage": "Rodada 2", "runner": "codex",
                    "model": "gpt-5.6-sol", "summary": "Aprofundar rascunho.",
                    "prompt": "Aprofundar", "outputs": ["r2.md"], "depends_on": ["r1_a2"]
                },
                {
                    "id": "r2_a2", "role": "Revisor", "stage": "Rodada 2", "runner": "gemini",
                    "model": "gemini-3.8-flash-high", "summary": "Validar final.",
                    "prompt": "Validar", "outputs": ["final.md"], "depends_on": ["r2_a1"]
                }
            ]
        }
        # 2 agents x 2 rounds = 4 tasks
        contracted = team_contract(plan, count=2, catalogs=catalogs, total_rounds=2)
        self.assertEqual(len(contracted["tasks"]), 4)

        # Mismatched count raises ValueError
        with self.assertRaises(ValueError):
            team_contract(plan, count=2, catalogs=catalogs, total_rounds=3)
