"""Atomic snapshots, append-only events, and an exclusive POSIX run lock."""
import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@contextmanager
def run_lock(run):
    with (run / ".lock").open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Esta execução já tem um coordenador ativo") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Store:
    def __init__(self, run):
        self.run = Path(run)
        self.state = read_json(self.run / "state.json")

    def save(self):
        self.state["updated_at"] = time.time()
        write_json(self.run / "state.json", self.state)

    def event(self, agent, kind, message):
        # Single asyncio writer; readers tolerate an incomplete last line.
        event = {"time": time.time(), "agent": agent, "kind": kind, "message": str(message)[:4000]}
        with (self.run / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        if agent in self.state["tasks"]:
            self.state["tasks"][agent]["activity"] = event["message"][:300]
        self.save()
