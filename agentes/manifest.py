"""Strict, dependency-free manifest validation. No code execution on load."""
import json
import re
from pathlib import PurePosixPath, Path


class ManifestError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ManifestError(message)


def relative_path(value):
    require(isinstance(value, str) and bool(value), "Caminho deve ser texto não vazio")
    p = PurePosixPath(value)
    require(not p.is_absolute() and all(x not in (".", "..", "") for x in value.split("/"))
            and "\\" not in value and ":" not in value,
            f"Caminho relativo inválido: {value}")
    require(not any(ord(c) < 32 for c in value), "Controle de terminal em caminho")
    return value


def validate(data):
    require(isinstance(data, dict), "Plano deve ser um objeto JSON v2 ou v3; veja docs/MIGRACAO.md")
    require(set(data) <= {"version", "objective", "tasks"}, "Campo desconhecido no plano")
    require(type(data.get("version")) is int and data["version"] in (2, 3), "version deve ser 2 ou 3")
    require(isinstance(data.get("objective"), str) and data["objective"].strip(), "objective obrigatório")
    tasks = data.get("tasks")
    require(isinstance(tasks, list) and 0 < len(tasks) <= 100, "Use de 1 a 100 tarefas")
    seen = set()
    for t in tasks:
        require(isinstance(t, dict), "Cada tarefa deve ser objeto")
        require(set(t) <= {"id", "role", "stage", "runner", "model", "summary", "prompt", "depends_on",
                           "outputs", "checks", "timeout", "retries"}, "Campo desconhecido em tarefa")
        name = t.get("id")
        require(isinstance(name, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", name), "id inválido")
        require(name not in seen and name != "coordinator", f"id duplicado/reservado: {name}")
        seen.add(name)
        for key in ("role", "stage", "prompt"):
            require(isinstance(t.get(key), str) and t[key].strip(), f"{name}: {key} obrigatório")
        if "summary" in t:
            require(isinstance(t["summary"], str) and 1 <= len(t["summary"].strip()) <= 140,
                    f"{name}: summary deve ter de 1 a 140 caracteres")
        require(t.get("runner") in ("codex", "gemini", "opencode", "claude", "demo"), f"{name}: runner inválido")
        require(t.get("model") is None or isinstance(t["model"], str) and t["model"].strip(), "model inválido")
        t.setdefault("depends_on", [])
        t.setdefault("timeout", 600)
        t.setdefault("retries", 0)
        t.setdefault("checks", [])
        require(type(t["timeout"]) is int and 1 <= t["timeout"] <= 86400, "timeout: 1..86400 segundos")
        require(type(t["retries"]) is int and 0 <= t["retries"] <= 3, "retries: 0..3")
        deps = t["depends_on"]
        require(isinstance(deps, list) and all(isinstance(d, str) for d in deps), "depends_on deve ser lista de IDs")
        require(len(deps) == len(set(deps)), "Dependência duplicada")
        outputs = t.get("outputs")
        require(isinstance(outputs, list) and outputs, f"{name}: outputs obrigatório")
        for path in outputs:
            relative_path(path)
            require(path.split('/')[0] not in {"inputs", ".git", ".codex", ".gemini", "AGENTS.md", "GEMINI.md"}, "Saída reservada")
        require(len(outputs) == len(set(outputs)), "Saída duplicada")
        for a in outputs:
            require(not any(b.startswith(a + "/") for b in outputs), "Saídas não podem ser arquivos e diretórios simultaneamente")
        require(isinstance(t["checks"], list), "checks deve ser lista")
        for c in t["checks"]:
            # Normalizar aliases comuns que modelos às vezes geram
            if c.get("kind") == "file_exists":
                c["kind"] = "contains"
                c.setdefault("text", "")
            elif c.get("kind") in ("content_contains", "string_contains", "regex"):
                c["kind"] = "contains"
                if "pattern" in c and "text" not in c:
                    c["text"] = str(c.pop("pattern"))
                elif "patterns" in c and "text" not in c:
                    c["text"] = str(c["patterns"][0]) if c["patterns"] else ""
            elif c.get("kind") == "json_schema":
                c["kind"] = "json"
            require(isinstance(c, dict) and set(c) <= {"kind", "path", "text", "field", "value", "patterns", "schema"}, "check inválido")
            require(c.get("kind") in ("python", "json", "contains", "plan", "json_equals"), "check kind inválido")
            require(c.get("path") in outputs, "check deve referenciar uma saída declarada")
            if c["kind"] == "json_equals":
                require(isinstance(c.get("field"), str) and c["field"] and "value" in c, "json_equals exige field e value")
            if c["kind"] == "contains":
                require(isinstance(c.get("text"), str), "contains exige text")
    by_id = {t["id"]: t for t in tasks}
    visited, visiting = set(), set()
    def visit(name):
        require(name in by_id, f"Dependência inexistente: {name}")
        require(name not in visiting, f"Ciclo de dependências: {name}")
        if name in visited:
            return
        visiting.add(name)
        for dep in by_id[name]["depends_on"]:
            visit(dep)
        visiting.remove(name)
        visited.add(name)
    for name in by_id:
        visit(name)
    return data


def load(path):
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))
