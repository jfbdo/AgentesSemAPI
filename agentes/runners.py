"""CLI adapters. Credentials remain owned by the official clients."""
import asyncio
import json
import os
import signal
import time
from pathlib import Path

API_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
           "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_GENAI_USE_VERTEXAI", "ANTHROPIC_API_KEY")


class RunnerError(RuntimeError):
    def __init__(self, message, category="error", provider=None):
        super().__init__(message)
        self.category = category
        self.provider = provider


def category(text):
    text = text.lower()
    if any(x in text for x in ("quota", "rate limit", "rate_limit", "429", "usage limit", "capacity")):
        return "quota"
    if any(x in text for x in ("unauthorized", "authentication", "not logged", "sign in", "login", "401")):
        return "auth"
    return "error"


def opencode_bin():
    import shutil
    return shutil.which("opencode") or str(Path.home() / ".opencode" / "bin" / "opencode")


def command(task, workspace, prompt=None):
    if task["runner"] == "codex":
        model = ["-m", task["model"]] if task.get("model") else []
        return ["codex", "exec", "--json", "--color", "never", "--skip-git-repo-check",
                "--sandbox", "workspace-write", "--ignore-user-config",
                "-c", 'model_provider="openai"', "-c", 'forced_login_method="chatgpt"',
                "-C", str(workspace), *model, "-"]
    if task["runner"] == "opencode":
        model = ["-m", task["model"]] if task.get("model") else []
        cmd = [opencode_bin(), "run", "--dir", str(workspace), "--format", "json", *model]
        if prompt:
            cmd.append(prompt)
        return cmd
    if task["runner"] == "claude":
        model = ["--model", task["model"]] if task.get("model") else []
        cmd = ["claude", "-p", prompt or "-", "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits", *model]
        return cmd
    # Google AI Pro/Ultra accounts moved from Gemini CLI to Antigravity CLI.
    # Keep runner="gemini" in plans as the provider name, but execute through agy.
    model = ["--model", task["model"]] if task.get("model") else []
    return ["agy", "--output-format", "stream-json", "--sandbox",
            "--add-dir", str(workspace), "--print-timeout", f"{task['timeout']}s",
            *model, "-p", prompt or "-"]


def normalize(event):
    """Only observable actions/messages; intentionally omit reasoning and system chatter."""
    if not isinstance(event, dict):
        return None
    kind = event.get("type", "")
    agy_kind = event.get("event", "")
    if agy_kind == "result":
        result = event.get("result", {})
        if result.get("status") != "SUCCESS":
            return "error", str(result.get("error") or f"Antigravity: {result.get('status', 'falha')}")
        denied = result.get("denied_actions") or []
        if denied:
            names = ", ".join(str(item.get('display_name') or item.get('action', 'ação'))
                              for item in denied if isinstance(item, dict))
            return "error", "Antigravity bloqueou uma permissão no modo headless: " + (names or "ação negada")
        if result.get("response"):
            return "message", str(result["response"])
        return None
    if agy_kind == "step_update":
        step = event.get("step_update", {})
        if step.get("step_type") == "tool":
            info = step.get("tool_info", {})
            tool = step.get("tool_name") or info.get("name", "ação")
            params = json.dumps(info.get("parameters", {}), ensure_ascii=False)[:500]
            return "action", f"Ferramenta: {tool} | {params}"
        if (step.get("step_type") == "agent_response" and
                step.get("state") == "DONE" and step.get("text_delta")):
            return "message", str(step["text_delta"])
        return None
    if kind in ("error", "turn.failed"):
        return "error", str(event.get("message") or event.get("error") or "Falha do provedor")
    if kind == "result":
        if event.get("status") in ("error", "failed"):
            return "error", str(event.get("error", "Falha do provedor"))
        if event.get("result"):
            return "message", str(event["result"])
    if kind == "message" and event.get("role") == "assistant":
        return "message", str(event.get("content", ""))
    if kind == "assistant":
        msg = event.get("message", {})
        if isinstance(msg, dict):
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type")
                        if btype == "text" and block.get("text"):
                            return "message", str(block["text"])
                        if btype == "tool_use":
                            tool = block.get("name", "ação")
                            params = json.dumps(block.get("input", {}), ensure_ascii=False)[:500]
                            return "action", f"Ferramenta: {tool} | {params}"
            elif isinstance(content, str) and content:
                return "message", content
        elif event.get("content"):
            return "message", str(event["content"])
    if kind == "content_block_delta":
        delta = event.get("delta", {})
        if isinstance(delta, dict) and delta.get("type") == "text_delta" and delta.get("text"):
            return "message", str(delta["text"])
    if kind == "tool_use":
        tool_name = event.get("tool_name") or event.get("name") or "ação"
        params = event.get("parameters") or event.get("input") or {}
        return "action", f"Ferramenta: {tool_name} | {json.dumps(params, ensure_ascii=False)[:500]}"
    if kind == "tool_result":
        return "action", f"Ferramenta finalizada: {event.get('status', 'resultado recebido')}"
    if kind.startswith("item."):
        item = event.get("item", {})
        if not isinstance(item, dict):
            return None
        item_type = item.get("type")
        if item_type == "agent_message":
            return "message", str(item.get("text", ""))
        if item_type == "command_execution":
            return "action", f"Comando: {item.get('command', '')} | {item.get('status', '')}"
        if item_type == "file_change":
            return "action", "Arquivos: " + ", ".join(str(c.get("path", "")) for c in item.get("changes", []))
        if item_type in ("mcp_tool_call", "web_search"):
            return "action", f"{item_type}: {item.get('tool', item.get('query', 'em andamento'))}"
    if kind == "text":
        part = event.get("part", {})
        if isinstance(part, dict) and part.get("text"):
            return "message", str(part["text"])
    if kind == "step_finish":
        part = event.get("part", {})
        if isinstance(part, dict) and part.get("reason") in ("error", "failed"):
            return "error", str(part.get("error") or "OpenCode encerrou com erro")
    return None


async def terminate_group(proc):
    # Kill descendants as well, even if the leader already exited.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    await asyncio.sleep(0.2)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await proc.wait()


async def execute(task, workspace, prompt, log_path, emit, demo_delay=1.0):
    if task["runner"] == "demo":
        await demo(task, workspace, emit, demo_delay)
        return
    present = [key for key in API_ENV if os.environ.get(key)]
    if present:
        raise RunnerError("Modo assinatura: remova estas variáveis do processo: " + ", ".join(present), "auth")
    if task["runner"] == "gemini":
        from .setup import prepare_antigravity_sandbox
        prepare_antigravity_sandbox(workspace=workspace)
        outputs = [str((Path(workspace) / name).resolve()) for name in task['outputs']]
        prompt += ("\nATENÇÃO AO SANDBOX ANTIGRAVITY: o shell virtual pode iniciar em uma pasta scratch. "
                   f"A pasta real desta tarefa é {Path(workspace).resolve()}. "
                   f"Leia inputs somente dentro desse caminho e grave as entregas exatamente nestes caminhos: "
                   f"{json.dumps(outputs, ensure_ascii=False)}. Não grave entregas na pasta scratch.\n")
    elif task["runner"] == "opencode":
        outputs = [str((Path(workspace) / name).resolve()) for name in task['outputs']]
        prompt += ("\nATENÇÃO OBRIGATÓRIA À PASTA DE TRABALHO: "
                   f"Sua pasta de trabalho exclusiva é {Path(workspace).resolve()}. "
                   "NÃO altere nem grave arquivos fora dessa pasta (não mexa na pasta pai e não altere plan.json do sistema). "
                   f"Grave as entregas solicitadas EXATAMENTE nestes caminhos absolutos: "
                   f"{json.dumps(outputs, ensure_ascii=False)}.\n")
    elif task["runner"] == "claude":
        outputs = [str((Path(workspace) / name).resolve()) for name in task['outputs']]
        prompt += ("\nATENÇÃO OBRIGATÓRIA À PASTA DE TRABALHO: "
                   f"Sua pasta de trabalho exclusiva é {Path(workspace).resolve()}. "
                   "NÃO altere nem grave arquivos fora dessa pasta (não mexa na pasta pai e não altere plan.json do sistema). "
                   f"Grave as entregas solicitadas EXATAMENTE nestes caminhos absolutos: "
                   f"{json.dumps(outputs, ensure_ascii=False)}.\n")
    proc = await asyncio.create_subprocess_exec(
        *command(task, workspace, prompt), cwd=workspace,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=4 * 1024 * 1024)
    errors, tail, diagnostics = [], [], []
    with Path(log_path).open("a", encoding="utf-8") as log:
        async def drain(stream, source):
            while line := await stream.readline():
                text = line.decode("utf-8", errors="replace").rstrip()
                log.write(json.dumps({"time": time.time(), "source": source, "raw": text}, ensure_ascii=False) + "\n")
                log.flush()
                tail.append(text[:2000])
                del tail[:-20]
                if source == "stderr" and text and len(diagnostics) < 5:
                    diagnostics.append(text[:500])
                if source == "stdout":
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    result = normalize(event)
                    if result:
                        emit(*result)
                        if result[0] == "error":
                            errors.append(result[1])
        async def feed():
            try:
                if task["runner"] == "codex":
                    proc.stdin.write(prompt.encode())
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()
        async def lifecycle():
            await asyncio.gather(feed(), drain(proc.stdout, "stdout"), drain(proc.stderr, "stderr"), proc.wait())
        try:
            await asyncio.wait_for(lifecycle(), task["timeout"])
        except asyncio.TimeoutError:
            raise RunnerError(f"Tempo limite de {task['timeout']}s excedido", "timeout", provider=task["runner"]) from None
        finally:
            await terminate_group(proc)
    if proc.returncode != 0 or errors:
        detail = "\n".join(errors or diagnostics or tail)[:1000]
        raise RunnerError(f"CLI encerrou com código {proc.returncode}. {detail}", category(detail), provider=task["runner"])


async def demo(task, workspace, emit, delay):
    for action in ("Lendo a tarefa e suas dependências", "Preparando a entrega", "Gravando os arquivos"):
        emit("action", action + " [simulação]")
        await asyncio.sleep(delay)
    for name in task["outputs"]:
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".py"):
            text = 'def somar(a, b):\n    return a + b\n'
        elif name.endswith(".json"):
            text = json.dumps({"demo": True, "task": task["id"]})
        else:
            text = f"# {task['role']}\n\nEntrega demonstrativa: {task['id']}.\n"
        path.write_text(text, encoding="utf-8")
    emit("message", "Entrega simulada pronta para validação.")
