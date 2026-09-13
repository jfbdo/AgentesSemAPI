import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agentes.manifest import validate
from agentes.runners import category, RunnerError
from agentes.engine import create_run, run_engine, fork_or_migrate_run
from agentes.monitor import render
from agentes.storage import read_json


class QuotaSwitchTests(unittest.TestCase):
    def test_quota_category_detection(self):
        err_codex = "CLI encerrou com código 1. You've hit your usage limit. Upgrade to Pro or try again at 9:42 PM."
        self.assertEqual(category(err_codex), "quota")
        err = RunnerError(err_codex, category(err_codex), provider="codex")
        self.assertEqual(err.category, "quota")
        self.assertEqual(err.provider, "codex")

    def test_manifest_accepts_v2_and_v3(self):
        plan_v2 = {
            "version": 2,
            "objective": "Teste v2",
            "tasks": [{"id": "t1", "role": "Dev", "stage": "Dev", "runner": "demo",
                       "prompt": "Fazer algo", "outputs": ["out.txt"], "checks": [], "timeout": 60, "retries": 1}]
        }
        self.assertEqual(validate(plan_v2)["version"], 2)

        plan_v3 = dict(plan_v2)
        plan_v3["version"] = 3
        self.assertEqual(validate(plan_v3)["version"], 3)

    def test_fork_or_migrate_run_preserves_succeeded_and_switches_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                "version": 2,
                "objective": "Pipeline com dois agentes",
                "tasks": [
                    {"id": "t1", "role": "Especialista 1", "stage": "Etapa 1", "runner": "demo",
                     "prompt": "Gerar out1.txt", "outputs": ["out1.txt"], "checks": [], "timeout": 60, "retries": 1},
                    {"id": "t2", "role": "Especialista 2", "stage": "Etapa 2", "runner": "demo",
                     "depends_on": ["t1"], "prompt": "Gerar out2.txt", "outputs": ["out2.txt"], "checks": [],
                     "timeout": 60, "retries": 1}
                ]
            }
            # Execute first run
            run1 = create_run(plan, root)
            status1 = asyncio.run(run_engine(run1, demo_delay=0.01))
            self.assertEqual(status1, "succeeded")

            # Simulate run where t2 was paused
            state1 = read_json(run1 / "state.json")
            state1["status"] = "paused"
            state1["exhausted_provider"] = "codex"
            state1["suggested_provider"] = "gemini"
            state1["tasks"]["t2"]["status"] = "paused"
            state1["tasks"]["t2"]["error"] = "You've hit your usage limit. Try again at 9:42 PM."
            (run1 / "state.json").write_text(json.dumps(state1), encoding="utf-8")

            # Fork/migrate run to demo with model demo-model
            run2 = fork_or_migrate_run(run1, target_provider="demo", target_model="demo-model", root=root)
            state2 = read_json(run2 / "state.json")
            plan2 = read_json(run2 / "plan.json")

            # t1 must be succeeded in state2 with verified hashes
            self.assertEqual(state2["tasks"]["t1"]["status"], "succeeded")
            self.assertTrue((run2 / "artifacts" / "t1" / "out1.txt").exists())

            # t2 must be pending and have new runner
            self.assertEqual(state2["tasks"]["t2"]["status"], "pending")
            task2_plan = next(t for t in plan2["tasks"] if t["id"] == "t2")
            self.assertEqual(task2_plan["runner"], "demo")
            self.assertEqual(task2_plan["model"], "demo-model")

            # Running run2 must succeed and complete t2
            status2 = asyncio.run(run_engine(run2, demo_delay=0.01))
            self.assertEqual(status2, "succeeded")
            self.assertTrue((run2 / "artifacts" / "t2" / "out2.txt").exists())

    def test_monitor_renders_quota_pause_banner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                "version": 2,
                "objective": "Objetivo teste",
                "tasks": [
                    {"id": "t1", "role": "Dev", "stage": "Dev", "runner": "codex",
                     "prompt": "Fazer tarefa", "outputs": ["out.txt"], "checks": [], "timeout": 60, "retries": 1}
                ]
            }
            run = create_run(plan, root)
            state = read_json(run / "state.json")
            state["status"] = "paused"
            state["exhausted_provider"] = "codex"
            state["suggested_provider"] = "gemini"
            state["tasks"]["t1"]["status"] = "paused"
            state["tasks"]["t1"]["error"] = "CLI encerrou com código 1. You've hit your usage limit."
            (run / "state.json").write_text(json.dumps(state), encoding="utf-8")

            output = render(run, width=90)
            self.assertIn("LIMITE DE USO ATINGIDO", output)
            self.assertIn("Google Antigravity (Gemini)", output)

    def test_live_quota_resolver_switches_model_and_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                "version": 2,
                "objective": "Objetivo teste de cota ao vivo",
                "tasks": [
                    {"id": "t1", "role": "Dev", "stage": "Dev", "runner": "codex",
                     "prompt": "Fazer tarefa", "outputs": ["out.txt"], "checks": [], "timeout": 60, "retries": 1}
                ]
            }
            run = create_run(plan, root)

            calls = []
            def mock_resolver(exhausted, task):
                calls.append((exhausted, task["id"]))
                return ("demo", "demo-fallback")

            import agentes.engine as eng
            orig_execute = eng.execute
            async def fake_execute(task, workspace, prompt, log_path, emit, delay):
                if task["runner"] == "codex":
                    raise RunnerError("You've hit your usage limit.", "quota", provider="codex")
                (workspace / "out.txt").write_text("conteúdo gerado", encoding="utf-8")

            eng.execute = fake_execute
            try:
                status = asyncio.run(run_engine(run, quota_resolver=mock_resolver))
                self.assertEqual(status, "succeeded")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0], ("codex", "t1"))
                state = read_json(run / "state.json")
                self.assertEqual(state["tasks"]["t1"]["status"], "succeeded")
                self.assertTrue((run / "artifacts" / "t1" / "out.txt").exists())
            finally:
                eng.execute = orig_execute

    def test_live_quota_resolver_pauses_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = {
                "version": 2,
                "objective": "Objetivo teste de pausa",
                "tasks": [
                    {"id": "t1", "role": "Dev", "stage": "Dev", "runner": "codex",
                     "prompt": "Fazer tarefa", "outputs": ["out.txt"], "checks": [], "timeout": 60, "retries": 1}
                ]
            }
            run = create_run(plan, root)

            def mock_resolver_pause(exhausted, task):
                return None

            import agentes.engine as eng
            orig_execute = eng.execute
            async def fake_execute(task, workspace, prompt, log_path, emit, delay):
                raise RunnerError("You've hit your usage limit.", "quota", provider="codex")

            eng.execute = fake_execute
            try:
                status = asyncio.run(run_engine(run, quota_resolver=mock_resolver_pause))
                self.assertEqual(status, "paused")
                state = read_json(run / "state.json")
                self.assertEqual(state["tasks"]["t1"]["status"], "paused")
                self.assertEqual(state["exhausted_provider"], "codex")
            finally:
                eng.execute = orig_execute


    def test_opencode_runner_and_manifest(self):
        from agentes.manifest import validate
        from agentes.runners import command, normalize
        plan = {
            "version": 3,
            "objective": "Teste OpenCode",
            "tasks": [
                {"id": "op_task", "role": "Dev", "stage": "Exec", "runner": "opencode",
                 "model": "opencode/nemotron-3.5-lightning-free", "prompt": "Criar código",
                 "outputs": ["out.py"], "checks": [], "timeout": 120, "retries": 0}
            ]
        }
        val = validate(plan)
        self.assertEqual(val["tasks"][0]["runner"], "opencode")
        cmd = command(val["tasks"][0], Path("/tmp/test_workspace"), "Olá")
        self.assertIn("run", cmd)
        self.assertIn("--format", cmd)
        self.assertIn("json", cmd)
        self.assertIn("opencode/nemotron-3.5-lightning-free", cmd)

        # Event normalization
        msg = normalize({"type": "text", "part": {"text": "Código concluído"}})
        self.assertEqual(msg, ("message", "Código concluído"))
        err = normalize({"type": "step_finish", "part": {"reason": "error", "error": "Falha na API"}})
        self.assertEqual(err, ("error", "Falha na API"))

    def test_claude_runner_and_manifest(self):
        from agentes.runners import command, normalize
        from agentes.monitor import provider_name
        from agentes.wizard import provider_name_for

        plan = {
            "version": 3,
            "objective": "Teste Anthropic Claude",
            "tasks": [
                {"id": "claude_task", "role": "Arquiteto", "stage": "Design", "runner": "claude",
                 "model": "claude-3-7-sonnet", "prompt": "Desenhar arquitetura",
                 "outputs": ["arquitetura.md"], "checks": [], "timeout": 120, "retries": 0}
            ]
        }
        val = validate(plan)
        task = val["tasks"][0]
        self.assertEqual(task["runner"], "claude")
        self.assertEqual(provider_name(task), "Anthropic Claude")
        self.assertEqual(provider_name_for("claude"), "Anthropic Claude")

        cmd = command(task, Path("/tmp/test_claude_ws"), "Desenhar arquitetura")
        self.assertEqual(cmd[0], "claude")
        self.assertIn("-p", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("stream-json", cmd)
        self.assertIn("--verbose", cmd)
        self.assertIn("--permission-mode", cmd)
        self.assertIn("acceptEdits", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("claude-3-7-sonnet", cmd)

        # Event normalization for Claude Code stream-json
        # Text block in assistant message
        claude_text_event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Arquitetura planejada com sucesso."}
                ]
            }
        }
        self.assertEqual(normalize(claude_text_event), ("message", "Arquitetura planejada com sucesso."))

        # Tool use in assistant message
        claude_tool_event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Write", "input": {"path": "arquitetura.md"}}
                ]
            }
        }
        ev_type, ev_text = normalize(claude_tool_event)
        self.assertEqual(ev_type, "action")
        self.assertIn("Write", ev_text)

        # Standalone tool use
        direct_tool = {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}
        self.assertEqual(normalize(direct_tool)[0], "action")

        # Delta stream
        delta_event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Detalhe"}}
        self.assertEqual(normalize(delta_event), ("message", "Detalhe"))


if __name__ == "__main__":
    unittest.main()


