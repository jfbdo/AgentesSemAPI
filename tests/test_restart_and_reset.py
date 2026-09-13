import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agentes.cli import demo_plan
from agentes.engine import create_run
from agentes.storage import read_json, write_json
from agentes.wizard import restart_run
from agentes.monitor import rebuild_dashboard_windows


class RestartAndResetTests(unittest.TestCase):
    def test_restart_run_resets_state_artifacts_and_work(self):
        with tempfile.TemporaryDirectory() as root:
            plan = demo_plan()
            run = create_run(plan, root)

            # simulate run having finished task 1 and failed task 2
            state = read_json(run / "state.json")
            state["status"] = "failed"
            state["tasks"]["backend"]["status"] = "succeeded"
            state["tasks"]["backend"]["attempts"] = 1
            state["tasks"]["backend"]["hashes"] = {"soma.py": "abc123"}
            state["tasks"]["documentacao"]["status"] = "failed"
            state["tasks"]["documentacao"]["error"] = "quota error"
            write_json(run / "state.json", state)

            # create dummy artifacts and work folders
            art_file = run / "artifacts" / "backend" / "soma.py"
            art_file.parent.mkdir(parents=True)
            art_file.write_text("def soma(): pass")

            work_file = run / "work" / "backend" / "1" / "temp.txt"
            work_file.parent.mkdir(parents=True)
            work_file.write_text("temp")

            # Call restart_run
            with patch("subprocess.run"):
                restarted = restart_run(run)

            self.assertEqual(restarted, run)

            # Verify state
            new_state = read_json(run / "state.json")
            self.assertEqual(new_state["status"], "ready")
            for t_id, t_state in new_state["tasks"].items():
                self.assertEqual(t_state["status"], "pending")
                self.assertEqual(t_state["attempts"], 0)
                self.assertIsNone(t_state["error"])
                self.assertEqual(t_state["hashes"], {})

            # Verify artifacts and work were wiped clean
            self.assertTrue((run / "artifacts").exists())
            self.assertEqual(list((run / "artifacts").iterdir()), [])
            self.assertTrue((run / "work").exists())
            self.assertEqual(list((run / "work").iterdir()), [])

    def test_rebuild_dashboard_windows_safe_when_no_tmux(self):
        with tempfile.TemporaryDirectory() as root:
            plan = demo_plan()
            run = create_run(plan, root)
            with patch("shutil.which", return_value=None):
                # Should safely return without error
                rebuild_dashboard_windows(run)
