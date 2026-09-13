import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentes.cli import demo_plan
from agentes.engine import create_run
from agentes.manifest import validate
from agentes.monitor import render
from agentes.review import (
    collect_deliverables,
    build_review_prompt,
    sanitize_review_tasks,
    review_with_coordinator,
)
from agentes.storage import read_json, write_json


class CoordinatorReviewTests(unittest.TestCase):
    def test_collect_deliverables_finds_files_and_ignores_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            run = create_run(demo_plan(), root)
            art_dir = run / "artifacts" / "tarefa1"
            art_dir.mkdir(parents=True)
            doc = art_dir / "entrega.md"
            doc.write_text("# Relatório\nConteúdo inicial da entrega.", encoding="utf-8")
            
            # create symlink which should be ignored
            sym = art_dir / "link.md"
            sym.symlink_to(doc)

            deliverables = collect_deliverables(run)
            self.assertEqual(len(deliverables), 1)
            task_id, files = deliverables[0]
            self.assertEqual(task_id, "tarefa1")
            self.assertEqual([f.name for f in files], ["entrega.md"])

    def test_build_review_prompt_includes_feedback_and_snippets(self):
        with tempfile.TemporaryDirectory() as root:
            run = create_run(demo_plan(), root)
            art_dir = run / "artifacts" / "tarefa1"
            art_dir.mkdir(parents=True)
            doc = art_dir / "entrega.md"
            doc.write_text("Amostra do texto produzido", encoding="utf-8")

            deliverables = collect_deliverables(run)
            prompt = build_review_prompt("Objetivo X", deliverables, "Aprofundar exemplos", {})
            self.assertIn("Objetivo X", prompt)
            self.assertIn("Amostra do texto produzido", prompt)
            self.assertIn("Aprofundar exemplos", prompt)
            self.assertIn("parecer.json", prompt)

    def test_sanitize_review_tasks_validates_and_defaults(self):
        catalogs = {
            "gemini": [{"id": "gemini-3.8-flash-high"}, {"id": "gemini-3.1-pro-high"}],
            "codex": [{"id": "gpt-5.6-sol"}],
        }
        raw = [
            {
                "id": "nova_tarefa_1",
                "role": "Revisor",
                "stage": "Revisão",
                "runner": "gemini",
                "model": "gemini-invalido",
                "summary": "Resumo da tarefa",
                "prompt": "Leia o arquivo e revise.",
                "outputs": ["resultado.md"],
            }
        ]
        sanitized = sanitize_review_tasks(raw, catalogs)
        self.assertEqual(len(sanitized), 1)
        t = sanitized[0]
        self.assertEqual(t["id"], "nova_tarefa_1")
        self.assertEqual(t["runner"], "gemini")
        # Should default to first valid catalog model when invalid is given
        self.assertEqual(t["model"], "gemini-3.8-flash-high")
        self.assertEqual(t["timeout"], 600)
        self.assertEqual(t["retries"], 1)
        self.assertEqual(t["checks"], [])

    def test_coordinator_banner_shows_review_button_on_success(self):
        with tempfile.TemporaryDirectory() as root:
            run = create_run(demo_plan(), root)
            state = read_json(run / "state.json")
            state["status"] = "succeeded"
            for task in state["tasks"].values():
                task["status"] = "succeeded"
            write_json(run / "state.json", state)

            rendered = render(run, width=100)
            self.assertIn("REVISAR COM COORDENADOR", rendered)
            self.assertIn("tecla r", rendered)

    def test_review_with_coordinator_creates_continuation_run(self):
        with tempfile.TemporaryDirectory() as root:
            plan = demo_plan()
            run = create_run(plan, root)
            
            # create completed artifacts
            art_dir = run / "artifacts" / "agente_demo"
            art_dir.mkdir(parents=True)
            art_file = art_dir / "entrega_1.md"
            art_file.write_text("Entrega demonstrativa original", encoding="utf-8")

            state = read_json(run / "state.json")
            state["status"] = "succeeded"
            write_json(run / "state.json", state)
            write_json(run / "team-selection.json", {
                "coordinator": {"runner": "demo", "model": "demo-coordenador"},
                "requested_agents": 2
            })

            # Mock user inputs: first provides feedback, then confirms new round, then Enter for models
            inputs = ["Preciso de mais detalhes práticos"]
            asks = ["s", ""]

            def mock_input(prompt):
                return inputs.pop(0)

            def mock_ask(prompt, default=""):
                return asks.pop(0) if asks else (default or "")

            with patch("shutil.which", return_value=None), \
                 patch("agentes.wizard.progress", return_value="succeeded"):
                continuation_run = review_with_coordinator(
                    run, root=root, prompt_fn=mock_input, ask_fn=mock_ask
                )

            self.assertIsNotNone(continuation_run)
            self.assertTrue(continuation_run.exists())

            # Check that continuation run has team-selection with parent_run
            team_sel = read_json(continuation_run / "team-selection.json")
            self.assertEqual(team_sel["source"], "coordinator_review")
            self.assertEqual(team_sel["parent_run"], str(run))

            # Check that previous artifact was imported as input
            new_state = read_json(continuation_run / "state.json")
            self.assertIn("entrega_1.md", new_state["user_inputs"])

    def test_detect_and_execute_direct_action_copies_file(self):
        with tempfile.TemporaryDirectory() as root:
            run = create_run(demo_plan(), root)
            art_dir = run / "artifacts" / "entrega_final"
            art_dir.mkdir(parents=True)
            doc = art_dir / "megabrain_final.md"
            doc.write_text("# MegaBrain Final\nConteúdo v2.2.0", encoding="utf-8")

            deliverables = [(art_dir.name, [doc])]
            dest_dir = Path(root) / "downloads_mock"

            parecer = {
                "avaliacao": "Solicitação para mover documento.",
                "necessita_nova_rodada": False,
                "acao_direta": {
                    "tipo": "copiar_arquivo",
                    "origem": "megabrain_final.md",
                    "destino": str(dest_dir)
                },
                "novas_tarefas": []
            }

            from agentes.review import detect_and_execute_direct_action
            res = detect_and_execute_direct_action("copie para a pasta de downloads", parecer, deliverables, run)
            self.assertTrue(res["executed"])
            copied_file = dest_dir / "megabrain_final.md"
            self.assertTrue(copied_file.exists())
            self.assertEqual(copied_file.read_text(encoding="utf-8"), "# MegaBrain Final\nConteúdo v2.2.0")

    def test_review_with_coordinator_direct_action_does_not_restart_queue(self):
        with tempfile.TemporaryDirectory() as root:
            plan = demo_plan()
            run = create_run(plan, root)
            art_dir = run / "artifacts" / "tarefa_1"
            art_dir.mkdir(parents=True)
            doc = art_dir / "megabrain_final.md"
            doc.write_text("Relatório final", encoding="utf-8")

            state = read_json(run / "state.json")
            state["status"] = "succeeded"
            write_json(run / "state.json", state)
            write_json(run / "team-selection.json", {
                "coordinator": {"runner": "demo", "model": "demo-coordenador"},
                "requested_agents": 1
            })

            inputs = ["Mova a saída final para Downloads"]

            def mock_input(prompt):
                return inputs.pop(0)

            with patch("shutil.which", return_value=None), \
                 patch("agentes.wizard.progress", return_value="succeeded"), \
                 patch("pathlib.Path.home", return_value=Path(root)), \
                 patch("os.path.expanduser", side_effect=lambda p: p.replace("~", str(root))):
                continuation_run = review_with_coordinator(
                    run, root=root, prompt_fn=mock_input
                )

            # Direct action must complete without restarting queue or creating continuation run
            self.assertIsNone(continuation_run)
            self.assertTrue((Path(root) / "Downloads" / "megabrain_final.md").exists())

