"""DAG scheduler, attempt isolation, artifact validation and crash recovery."""
import ast
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from .manifest import load, validate
from .runners import execute, RunnerError
from .storage import Store, read_json, write_json, run_lock


def create_run(plan, root, input_paths=None):
    plan = validate(plan)
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run = root / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    run.mkdir(mode=0o700)
    write_json(run / "plan.json", plan)
    imported = []
    if input_paths:
        destination = run / "user_inputs"
        destination.mkdir()
        for original in input_paths:
            candidate = Path(original).expanduser()
            if candidate.is_symlink():
                raise ValueError(f"Link simbólico não permitido como entrada: {original}")
            source = candidate.resolve()
            if not source.is_file():
                raise ValueError(f"Entrada deve ser um arquivo regular: {original}")
            if source.stat().st_size > 50 * 1024 * 1024:
                raise ValueError(f"Entrada excede 50 MiB: {source.name}")
            name = source.name
            target = destination / name
            suffix = 2
            while target.exists():
                target = destination / f"{source.stem}-{suffix}{source.suffix}"
                suffix += 1
            shutil.copyfile(source, target)
            imported.append(target.name)
    write_json(run / "state.json", {
        "status": "ready", "created_at": time.time(), "objective": plan["objective"],
        "plan_hash": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
        "user_inputs": imported,
        "tasks": {t["id"]: {"status": "pending", "attempts": 0, "activity": "Aguardando início",
                              "error": None, "hashes": {}} for t in plan["tasks"]}})
    (run / "logs").mkdir()
    return run


def safe_file(root, name):
    path = root / name
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError(f"Link simbólico não permitido: {name}")
        current = current.parent
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Saída ausente ou vazia: {name}")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError(f"Saída excede 20 MiB: {name}")
    return path


def check_outputs(task, workspace):
    for name in task["outputs"]:
        safe_file(workspace, name)
    for check in task["checks"]:
        path = safe_file(workspace, check["path"])
        text = path.read_text(encoding="utf-8")
        kind = check["kind"]
        if kind == "python":
            ast.parse(text, filename=check["path"])
        elif kind == "json":
            json.loads(text)
        elif kind == "json_equals":
            data = json.loads(text)
            if not isinstance(data, dict) or check["field"] not in data or type(data[check["field"]]) is not type(check["value"]) or data[check["field"]] != check["value"]:
                raise ValueError(f"{check['path']}: critério {check['field']} reprovado")
        elif kind == "plan":
            validate(json.loads(text))
        elif check["text"] not in text:
            raise ValueError(f"{check['path']}: texto esperado ausente: {check['text']}")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_for(plan, task, previous_error=None):
    prompt = (
        f"Objetivo geral: {plan['objective']}\nSeu papel: {task['role']}\n"
        f"Etapa: {task['stage']}\nTarefa: {task['prompt']}\n"
        f"Entregas obrigatórias (relativas à pasta atual): {json.dumps(task['outputs'])}\n"
        f"Critérios automáticos: {json.dumps(task['checks'], ensure_ascii=False)}\n"
        "Trabalhe somente nesta pasta. Arquivos fornecidos pelo usuário ficam em inputs/usuario/. "
        "Entregas anteriores ficam em inputs/<id-da-dependência>/. "
        "Leia essas entradas e produza suas próprias saídas; não modifique inputs. "
        "Não publique, não envie mensagens externas, não leia credenciais, não altere autenticação. "
        "Não crie subagentes: o coordenador controla a concorrência. "
        "Não afirme ter testado sem executar o teste. Informe limitações na resposta final.\n")
    if previous_error:
        prompt += f"Tentativa anterior falhou; corrija este problema: {previous_error}\n"
    return prompt


async def run_engine(run, limits=None, demo_delay=1.0, quota_resolver=None):
    run = Path(run).resolve()
    limits = limits or {"total": 3, "codex": 1, "gemini": 1, "opencode": 2, "claude": 1, "demo": 3}
    limits.setdefault("opencode", 2)
    limits.setdefault("claude", 1)
    with run_lock(run):
        plan = load(run / "plan.json")
        store = Store(run)
        state = store.state
        if set(state["tasks"]) != {t["id"] for t in plan["tasks"]}:
            raise ValueError("Plano e estado divergentes; crie uma nova execução")
        if state.get("plan_hash") != hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest():
            raise ValueError("Plano alterado; crie uma nova execução")
        # Completed artifacts are immutable inputs. Refuse silent mutation on resume.
        for task in plan["tasks"]:
            s = state["tasks"][task["id"]]
            if s["status"] == "succeeded":
                for name, expected in s["hashes"].items():
                    path = safe_file(run / "artifacts" / task["id"], name)
                    if digest(path) != expected:
                        raise ValueError(f"Artefato alterado: {task['id']}/{name}. Crie nova execução")
            else:
                s.update(status="pending", error=None)
        state["status"] = "running"
        state["pid"] = os.getpid()
        store.event("coordinator", "status", "Coordenador ativo; dependências verificadas")
        active = {}
        counts = {r: 0 for r in ("codex", "gemini", "opencode", "claude", "demo")}
        quota_pause = False

        async def worker(task):
            name = task["id"]
            s = state["tasks"][name]
            try:
                for retry in range(task["retries"] + 1):
                    previous_error = s.get("error")
                    s.pop("finished_at", None)
                    s.update(status="running", attempts=s["attempts"] + 1, started_at=time.time(), error=None)
                    workspace = run / "work" / name / str(s["attempts"])
                    workspace.mkdir(parents=True, exist_ok=False)
                    s["workspace"] = str(workspace)
                    if (run / "user_inputs").exists():
                        shutil.copytree(run / "user_inputs", workspace / "inputs" / "usuario")
                    for dep in task["depends_on"]:
                        shutil.copytree(run / "artifacts" / dep, workspace / "inputs" / dep)
                    for output in task["outputs"]:
                        (workspace / output).parent.mkdir(parents=True, exist_ok=True)
                    emit = lambda kind, message: store.event(name, kind, message)
                    emit("status", f"{task['role']} iniciou a tentativa {s['attempts']}")
                    try:
                        await execute(task, workspace, prompt_for(plan, task, previous_error),
                                      run / "logs" / f"{name}-{s['attempts']}.jsonl", emit, demo_delay)
                        s["status"] = "validating"
                        emit("status", "Validando entregas e critérios de aceitação")
                        # Se o modelo salvou a saída na pasta da execução por engano em vez do workspace
                        for output in task["outputs"]:
                            target_out = workspace / output
                            if not target_out.exists():
                                misplaced = run / output
                                if misplaced.is_file() and misplaced.stat().st_size > 0:
                                    target_out.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.copyfile(misplaced, target_out)
                                    # Restaurar o plan.json original da execução se o runner tiver sobrescrito
                                    if output == "plan.json" and misplaced == (run / "plan.json"):
                                        write_json(run / "plan.json", plan)
                        check_outputs(task, workspace)
                        artifact = run / "artifacts" / name
                        staging = run / "artifacts" / f".{name}-{uuid.uuid4().hex}"
                        staging.mkdir(parents=True)
                        hashes = {}
                        for output in task["outputs"]:
                            source = safe_file(workspace, output)
                            target = staging / output
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, target)
                            hashes[output] = digest(target)
                        if artifact.exists():
                            shutil.rmtree(artifact)
                        os.replace(staging, artifact)
                        s.update(status="succeeded", hashes=hashes, finished_at=time.time(), error=None)
                        emit("status", "Entrega validada e disponível para as próximas tarefas")
                        return
                    except (RunnerError, ValueError, SyntaxError, OSError) as exc:
                        reason = getattr(exc, "category", "error")
                        exhausted = getattr(exc, "provider", task.get("runner")) if reason == "quota" else None
                        suggested = ("gemini" if exhausted == "codex" else "codex") if exhausted else None
                        emit("error", str(exc))
                        if reason == "quota" and quota_resolver:
                            decision = None
                            try:
                                if asyncio.iscoroutinefunction(quota_resolver):
                                    decision = await quota_resolver(exhausted, task)
                                else:
                                    decision = quota_resolver(exhausted, task)
                            except Exception as r_err:
                                emit("error", f"Falha no resolvedor de cota: {r_err}")
                            if decision:
                                new_runner, new_model = decision
                                old_runner = task["runner"]
                                task["runner"] = new_runner
                                task["model"] = new_model
                                counts[old_runner] = max(0, counts[old_runner] - 1)
                                counts[new_runner] = counts.get(new_runner, 0) + 1
                                cur = asyncio.current_task()
                                if cur in active:
                                    active[cur] = new_runner
                                emit("status", f"Cota esgotada em {old_runner}. Alternado para {new_runner} (modelo {new_model or 'padrão'}). Tentando novamente...")
                                for t_plan in plan["tasks"]:
                                    if t_plan["id"] == task["id"]:
                                        t_plan["runner"] = new_runner
                                        if new_model:
                                            t_plan["model"] = new_model
                                        else:
                                            t_plan.pop("model", None)
                                write_json(run / "plan.json", plan)
                                state["plan_hash"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
                                write_json(run / "state.json", state)
                                await asyncio.sleep(0.5)
                                continue
                        s.update(error=str(exc), status="paused" if reason in ("quota", "auth") else "failed",
                                 finished_at=time.time(), reason=reason,
                                 exhausted_provider=exhausted, suggested_provider=suggested)
                        if reason == "quota" and exhausted:
                            prov_name = "Codex" if exhausted == "codex" else "Google Antigravity"
                            sug_name = "Google Antigravity (Gemini)" if suggested == "gemini" else "Codex"
                            emit("status", f"Pausado por limite de cota em {prov_name}. Sugestão: alternar para {sug_name}.")
                        if s["status"] == "paused" or retry == task["retries"]:
                            return
                        emit("status", "Nova tentativa com contexto do erro anterior")
                        await asyncio.sleep(min(2 ** retry, 8))
            except asyncio.CancelledError:
                s.update(status="interrupted", finished_at=time.time())
                store.event(name, "status", "Interrompido; pode ser retomado")
                raise
            except Exception as exc:
                s.update(status="failed", error=str(exc), finished_at=time.time())
                store.event(name, "error", str(exc))

        try:
            while True:
                for task in plan["tasks"]:
                    s = state["tasks"][task["id"]]
                    if s["status"] != "pending":
                        continue
                    deps = [state["tasks"][d]["status"] for d in task["depends_on"]]
                    if any(d in ("failed", "blocked") for d in deps):
                        s["status"] = "blocked"
                        store.event(task["id"], "status", "Bloqueado por falha de uma dependência")
                        continue
                    if quota_pause or not all(d == "succeeded" for d in deps):
                        continue
                    runner = task["runner"]
                    if len(active) < limits["total"] and counts[runner] < limits[runner]:
                        s["status"] = "queued"
                        future = asyncio.create_task(worker(task))
                        active[future] = runner
                        counts[runner] += 1
                if not active:
                    break
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    counts[active.pop(future)] -= 1
                    await future
                quota_pause = any(s["status"] == "paused" for s in state["tasks"].values())
            statuses = [s["status"] for s in state["tasks"].values()]
            state["status"] = ("succeeded" if all(s == "succeeded" for s in statuses)
                               else "paused" if quota_pause else "failed")
            if quota_pause:
                paused_tasks = [s for s in state["tasks"].values() if s.get("status") == "paused"]
                exhausted = next((s.get("exhausted_provider") for s in paused_tasks if s.get("exhausted_provider")), None)
                if not exhausted:
                    for s in paused_tasks:
                        err = str(s.get("error") or "").lower()
                        if any(x in err for x in ("usage limit", "quota", "rate limit", "429")):
                            exhausted = "codex" if ("codex" in err or "chatgpt" in err) else "gemini"
                            break
                if exhausted:
                    state["exhausted_provider"] = exhausted
                    state["suggested_provider"] = "gemini" if exhausted == "codex" else "codex"
        except asyncio.CancelledError:
            for future in active:
                future.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            state["status"] = "interrupted"
            raise
        finally:
            state.pop("pid", None)
            store.event("coordinator", "status", f"Execução: {state['status']}")
            write_json(run / "result.json", state)
        return state["status"]


def fork_or_migrate_run(run, target_provider=None, target_model=None, root=None, task_overrides=None):
    """
    Cria uma nova execução limpa a partir de uma execução pausada, interrompida ou com falha.
    - Preserva e valida as entregas e hashes das tarefas que já foram concluídas (succeeded).
    - Aplica target_provider e target_model nas tarefas não concluídas (ou conforme task_overrides).
    - Preserva a imutabilidade do plano original e cria uma execução rastreável.
    """
    run = Path(run).resolve()
    plan = load(run / "plan.json")
    state = read_json(run / "state.json")
    root = Path(root or run.parent).resolve()

    new_tasks = []
    for task in plan["tasks"]:
        t = dict(task)
        s = state.get("tasks", {}).get(t["id"], {})
        if s.get("status") == "succeeded":
            new_tasks.append(t)
            continue
        if target_provider:
            t["runner"] = target_provider
            if target_model:
                t["model"] = target_model
            else:
                t.pop("model", None)
        if task_overrides and t["id"] in task_overrides:
            t.update(task_overrides[t["id"]])
        new_tasks.append(t)

    new_plan = {
        "version": plan.get("version", 2),
        "objective": plan["objective"],
        "tasks": new_tasks
    }
    new_plan = validate(new_plan)

    user_inputs_dir = run / "user_inputs"
    input_paths = [p for p in user_inputs_dir.iterdir() if p.is_file()] if user_inputs_dir.exists() else None

    new_run = create_run(new_plan, root, input_paths=input_paths)
    new_store = Store(new_run)
    new_state = new_store.state

    for task in plan["tasks"]:
        s = state.get("tasks", {}).get(task["id"], {})
        if s.get("status") == "succeeded":
            src_art = run / "artifacts" / task["id"]
            dst_art = new_run / "artifacts" / task["id"]
            if src_art.exists():
                shutil.copytree(src_art, dst_art, dirs_exist_ok=True)
            for output, expected in s.get("hashes", {}).items():
                target_file = safe_file(dst_art, output)
                if digest(target_file) != expected:
                    raise ValueError(f"Corrupção de artefato herdado: {task['id']}/{output}")
            new_state["tasks"][task["id"]].update(
                status="succeeded",
                attempts=s.get("attempts", 1),
                activity="Entrega validada herdada de execução anterior",
                hashes=s.get("hashes", {}),
                started_at=s.get("started_at", time.time()),
                finished_at=s.get("finished_at", time.time()),
                error=None
            )
            new_store.event(task["id"], "status", "Entrega herdada e validada com sucesso")

    new_store.event("coordinator", "status",
                    f"Execução continuada a partir de {run.name}. Alternado para {target_provider or 'novo plano'}")
    write_json(new_run / "state.json", new_state)
    return new_run
