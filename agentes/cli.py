"""Public CLI. Run with python3 -m agentes; installation is optional."""
import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from .engine import create_run, run_engine
from .manifest import load, validate
from .monitor import watch, dashboard
from .runners import API_ENV
from .storage import Store, read_json, run_lock


def demo_plan():
    return validate({"version": 2, "objective": "Demonstração: três agentes com monitoramento individual",
        "tasks": [
            {"id": "backend", "role": "Desenvolvedor backend", "stage": "1. Implementação", "runner": "demo",
             "summary": "Criar a função de soma.",
             "prompt": "Criar uma função de soma", "outputs": ["soma.py"],
             "checks": [{"kind": "python", "path": "soma.py"}]},
            {"id": "documentacao", "role": "Redator técnico", "stage": "1. Implementação", "runner": "demo",
             "summary": "Documentar como usar a função.",
             "prompt": "Documentar o exemplo", "outputs": ["guia.md"]},
            {"id": "revisor", "role": "Revisor", "stage": "2. Revisão", "runner": "demo",
             "summary": "Revisar o código e a documentação.",
             "prompt": "Revisar as entregas anteriores", "depends_on": ["backend", "documentacao"],
             "outputs": ["revisao.json"], "checks": [{"kind": "json", "path": "revisao.json"}]}]})


def limits_from(args):
    return {"total": args.workers, "codex": args.codex_workers, "gemini": args.gemini_workers,
            "opencode": getattr(args, "opencode_workers", 2),
            "claude": getattr(args, "claude_workers", 1),
            "demo": args.workers}


async def foreground(run, limits, delay):
    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, current.cancel)
    from .wizard import prompt_model_switch
    resolver = prompt_model_switch if sys.stdin.isatty() else None
    try:
        return await run_engine(run, limits, delay, quota_resolver=resolver)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def launch(run, args):
    print(f"Execução: {run}", flush=True)
    if args.background or args.dashboard:
        opencode_w = getattr(args, "opencode_workers", 2)
        claude_w = getattr(args, "claude_workers", 1)
        cmd = [sys.executable, "-m", "agentes", "resume", str(run),
               "--workers", str(args.workers), "--codex-workers", str(args.codex_workers),
               "--gemini-workers", str(args.gemini_workers), "--opencode-workers", str(opencode_w),
               "--claude-workers", str(claude_w),
               "--demo-delay", str(args.demo_delay)]
        with (run / "coordinator.log").open("a") as log:
            subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent.parent,
                             stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        if args.dashboard:
            dashboard(run)
        else:
            print(f"Monitor: python3 -m agentes watch {run}")
        return 0
    try:
        status = asyncio.run(foreground(run, limits_from(args), args.demo_delay))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Execução interrompida. Use resume para continuar.")
        return 130
    watch(run, once=True)
    return 0 if status == "succeeded" else 2


def positive(value):
    n = int(value)
    if not 1 <= n <= 32:
        raise argparse.ArgumentTypeError("Use um inteiro de 1 a 32")
    return n


def duration(value):
    n = float(value)
    if not 0.0 <= n <= 60.0:
        raise argparse.ArgumentTypeError("Use um número de 0 a 60")
    return n


def execution_options(parser):
    parser.add_argument("--workers", type=positive, default=3)
    parser.add_argument("--codex-workers", type=positive, default=1)
    parser.add_argument("--gemini-workers", type=positive, default=1)
    parser.add_argument("--opencode-workers", type=positive, default=2)
    parser.add_argument("--claude-workers", type=positive, default=1)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--dashboard", action="store_true", help="Inicia em background e abre tmux")
    parser.add_argument("--demo-delay", type=duration, default=1, help=argparse.SUPPRESS)


def doctor():
    from .ui import box_header, table, dim, bold, badge
    from .runners import opencode_bin
    from .setup import node_major

    py_ver = sys.version.split()[0]
    n_ver = node_major()

    print(box_header("⚙️  DIAGNÓSTICO DO AMBIENTE & FERRAMENTAS", f"Python {py_ver} • {sys.platform} • Node v{n_ver}"))
    print()

    headers = ["FERRAMENTA", "STATUS", "LOCALIZAÇÃO"]
    rows = []
    tools = [
        ("codex", "OpenAI Codex CLI"),
        ("agy", "Google Antigravity CLI"),
        ("opencode", "OpenCode CLI"),
        ("claude", "Anthropic Claude Code CLI"),
        ("tmux", "Terminal Multiplexer"),
        ("git", "Controle de Versão"),
        ("node", "Node.js Runtime"),
        ("npm", "Gerenciador de Pacotes Node")
    ]
    for name, _ in tools:
        if name == "opencode":
            path = opencode_bin() if Path(opencode_bin()).exists() else shutil.which("opencode")
        else:
            path = shutil.which(name)
        
        if path:
            st = badge("✓ Instalado", "success")
            loc = dim(str(path))
        else:
            st = badge("✗ Não instalado", "error")
            loc = dim("não instalado")
        rows.append([bold(name), st, loc])

    print(table(headers, rows))
    print()

    keys = [key for key in API_ENV if os.environ.get(key)]
    if keys:
        mode_status = badge("⚠️ Variáveis Detectadas", "warning") + f" (remova {', '.join(keys)})"
    else:
        mode_status = badge("✓ Modo Assinatura", "success") + " " + dim("(sem variáveis de API detectadas)")

    print(f"  {bold('Modo Assinatura')} : {mode_status}")
    print(f"  {bold('Autenticação')}    : {dim('codex login (ChatGPT) • agy (Google AI Pro/Ultra) • claude auth login (Claude Pro)')}")
    print(f"  {bold('Provedor Google')} : {dim('Acessado via Antigravity CLI oficial')}")
    print(f"  {bold('Privacidade')}     : {dim('Este diagnóstico não acessa credenciais, cotas nem faz chamadas aos modelos')}")



def stop(run):
    # The run lock distinguishes a live coordinator from a stale PID snapshot.
    try:
        with run_lock(run):
            print("Nenhum coordenador ativo; use resume para recuperar uma execução interrompida.")
            return
    except RuntimeError:
        state = read_json(run / "state.json")
        pid = state.get("pid")
        if not isinstance(pid, int) or pid <= 1:
            raise RuntimeError("Coordenador iniciando/finalizando; tente novamente")
        os.kill(pid, signal.SIGTERM)
        print("Interrupção solicitada. As entregas concluídas serão preservadas.")


def planner(args):
    # Planning is a normal monitored task, but its artifact must itself validate as a v2 plan.
    example = demo_plan()
    for task in example["tasks"]:
        task["runner"] = "codex" if task["id"] != "documentacao" else "gemini"
    instruction = (
        "Crie plan.json com um plano executável para o objetivo abaixo. Não execute o plano. "
        "Use o formato exato do exemplo, adaptando tarefas, papéis, dependências e critérios. "
        "Use somente runners codex/gemini; não fixe modelos; até 8 tarefas; sem ciclos. "
        "Cada tarefa tem pasta própria e recebe SOMENTE as saídas das dependências diretas em inputs/<id>/. "
        "Inclua uma tarefa final de integração que declare dependências de todas as entregas necessárias "
        "e produza o resultado consolidado. Escolha checks python/json/contains conforme as saídas. "
        "Use retries=1 e timeout=600. A tarefa de revisão deve exigir evidências, não aprovação automática.\n"
        f"Exemplo: {json.dumps(example, ensure_ascii=False)}\nObjetivo: {args.objective}")
    task = {"id": "planejador", "role": "Coordenador / planejador", "stage": "Planejamento",
            "runner": args.runner, "prompt": instruction, "outputs": ["plan.json"],
            "checks": [{"kind": "plan", "path": "plan.json"}], "timeout": 600, "retries": 1}
    plan = validate({"version": 2, "objective": args.objective, "tasks": [task]})
    run = create_run(plan, args.runs_dir)
    print(f"Plano gerado ficará em: {run / 'artifacts/planejador/plan.json'}")
    print("Após revisar, execute esse arquivo com o comando run.")
    return launch(run, args)


def main(argv=None):
    from .setup import activate
    activate()
    parser = argparse.ArgumentParser(description="AgentesSemAPI — equipes locais com painéis individuais")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="Assistente interativo de atividades")
    sub.add_parser("doctor", help="Verificar dependências sem consumir assinatura")
    v = sub.add_parser("validate", help="Validar plano sem executar")
    v.add_argument("plan", type=Path)
    d = sub.add_parser("demo", help="Demonstração offline com três agentes")
    d.add_argument("--runs-dir", type=Path, default=Path("runs"))
    execution_options(d)
    r = sub.add_parser("run", help="Executar plano JSON")
    r.add_argument("plan", type=Path)
    r.add_argument("--runs-dir", type=Path, default=Path("runs"))
    execution_options(r)
    r = sub.add_parser("resume", help="Retomar tarefas não concluídas")
    r.add_argument("run", type=Path)
    execution_options(r)
    p = sub.add_parser("plan", help="Pedir um plano ao coordenador IA")
    p.add_argument("objective")
    p.add_argument("--runner", choices=["codex", "gemini"], default="codex")
    p.add_argument("--runs-dir", type=Path, default=Path("runs"))
    execution_options(p)
    p_sw = sub.add_parser("switch", help="Alternar tarefas não concluídas para outro provedor (cota esgotada)")
    p_sw.add_argument("run", type=Path, help="Caminho da execução pausada")
    p_sw.add_argument("--to", choices=["gemini", "codex", "opencode", "claude"], required=True, help="Novo provedor para as tarefas pendentes/pausadas")
    p_sw.add_argument("--model", help="Modelo específico no novo provedor (opcional)")
    execution_options(p_sw)
    for cmd in ("watch", "dashboard", "stop", "help"):
        p = sub.add_parser(cmd)
        if cmd != "help":
            p.add_argument("run", type=Path)
        if cmd == "watch":
            p.add_argument("--agent")
            p.add_argument("--once", action="store_true")
        if cmd == "dashboard":
            p.add_argument("--no-attach", action="store_true")
            p.add_argument("--rebuild", action="store_true", help="Reconstrói as janelas e painéis do dashboard")
    args = parser.parse_args(argv)
    try:
        if getattr(args, "dashboard", False) and not shutil.which("tmux"):
            raise RuntimeError("Instale tmux para --dashboard, ou use --background e watch em terminais separados")
        if args.command in (None, "start"):
            from .wizard import start
            start()
        elif args.command == "doctor":
            doctor()
        elif args.command == "validate":
            plan = load(args.plan)
            print(f"Plano válido: {len(plan['tasks'])} tarefas")
        elif args.command in ("demo", "run"):
            plan = demo_plan() if args.command == "demo" else load(args.plan)
            run = create_run(plan, args.runs_dir)
            sys.exit(launch(run, args))
        elif args.command == "resume":
            run_dir = args.run.resolve()
            state = read_json(run_dir / "state.json")
            if sys.stdin.isatty() and not (getattr(args, "background", False) or getattr(args, "dashboard", False)) and state.get("status") == "paused":
                exhausted = state.get("exhausted_provider")
                for s in state.get("tasks", {}).values():
                    if not exhausted and s.get("status") == "paused" and s.get("error"):
                        err = str(s["error"]).lower()
                        if any(w in err for w in ("usage limit", "quota", "rate limit", "429")):
                            exhausted = "codex" if ("codex" in err or "chatgpt" in err) else "gemini"
                            break
                if exhausted:
                    from .wizard import prompt_model_switch
                    from .engine import fork_or_migrate_run
                    decision = prompt_model_switch(exhausted)
                    if decision:
                        target_provider, chosen_model = decision
                        run_dir = fork_or_migrate_run(run_dir, target_provider, chosen_model)
                        print(f"Execução continuada criada em: {run_dir}")
            sys.exit(launch(run_dir, args))
        elif args.command == "switch":
            from .engine import fork_or_migrate_run
            new_run = fork_or_migrate_run(args.run.resolve(), target_provider=args.to, target_model=args.model)
            print(f"Execução continuada criada em: {new_run}")
            print(f"Provedor alternado para: {args.to}")
            sys.exit(launch(new_run, args))
        elif args.command == "watch":
            watch(args.run, args.agent, args.once)
        elif args.command == "dashboard":
            if getattr(args, "rebuild", False):
                from .monitor import rebuild_dashboard_windows
                rebuild_dashboard_windows(args.run)
            else:
                dashboard(args.run, not args.no_attach)
        elif args.command == "stop":
            stop(args.run.resolve())
        elif args.command == "help":
            from .monitor import help_screen
            help_screen()
        elif args.command == "plan":
            sys.exit(planner(args))
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        sys.exit(1)
