"""Read-only terminal views and detachable tmux dashboard."""
import curses
import unicodedata
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .storage import read_json, write_json, Store, run_lock

LABELS = {"ready": "PRONTO", "pending": "AGUARDANDO", "queued": "NA FILA", "running": "EXECUTANDO",
          "validating": "VALIDANDO", "succeeded": "CONCLUÍDO", "failed": "FALHOU", "blocked": "BLOQUEADO",
          "paused": "PAUSADO", "interrupted": "INTERROMPIDO"}


def clean(text):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text))
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32 and ord(c) != 127)


def brief(text, maximum=180):
    value = " ".join(clean(text).split())
    return value if len(value) <= maximum else value[:maximum - 1].rstrip() + "…"


def recent_events(run, agent, maximum=8):
    path = run / "events.jsonl"
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 128 * 1024))
        if size > 128 * 1024:
            f.readline()
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event["agent"] == agent:
            stamp = time.strftime("%H:%M:%S", time.localtime(event["time"]))
            events.append(f"{stamp}  {brief(event['message'])}")
    return events[-maximum:]


ICONS = {"succeeded": "✅", "failed": "❌", "blocked": "⛔", "paused": "⏸",
         "running": "▶", "validating": "🔎", "pending": "⏳", "queued": "⏳",
         "ready": "○", "interrupted": "⏸"}


def status(value):
    return f"{ICONS.get(value, '○')} {LABELS[value]}"


def model_name(task):
    return task.get('model') or 'padrão do cliente'


def provider_name(task):
    if task['runner'] == 'demo':
        return 'Simulação local'
    if task['runner'] == 'gemini':
        return 'Google Antigravity'
    if task['runner'] == 'opencode':
        return 'OpenCode'
    if task['runner'] == 'claude':
        return 'Anthropic Claude'
    return 'Codex'


def task_summary(task):
    return task.get('summary') or f"Atuar como {task['role']} na etapa {task['stage']}."


def artifacts_uri(run):
    return (Path(run).resolve() / 'artifacts').as_uri()


def open_artifacts(run):
    folder = str(Path(run).resolve() / 'artifacts')
    opener = shutil.which('xdg-open') or shutil.which('open')
    if not opener:
        return False
    subprocess.Popen([opener, folder], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def copy_to_clipboard(text):
    import base64
    try:
        encoded = base64.b64encode(text.encode('utf-8')).decode('ascii')
        if os.environ.get("TMUX"):
            osc = f"\033Ptmux;\033\033]52;c;{encoded}\a\033\\"
        else:
            osc = f"\033]52;c;{encoded}\a"
        sys.stdout.write(osc)
        sys.stdout.flush()
    except Exception:
        pass
    for cmd in (["xclip", "-selection", "clipboard"], ["wl-copy"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, check=False)
                break
            except Exception:
                pass


def switch_agent_and_resume(run, agent_id, target_provider, target_model):
    run = Path(run).resolve()
    state_file = run / "state.json"
    state = read_json(state_file)

    # Encerra suavemente qualquer coordenador rodando para liberar o lock
    pid = state.get("pid")
    if isinstance(pid, int) and pid > 1:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(25):
                time.sleep(0.1)
                try:
                    with run_lock(run):
                        break
                except RuntimeError:
                    pass
        except OSError:
            pass

    plan = read_json(run / "plan.json")
    for task in plan["tasks"]:
        if task["id"] == agent_id:
            task["runner"] = target_provider
            if target_model:
                task["model"] = target_model
            else:
                task.pop("model", None)
    write_json(run / "plan.json", plan)

    state = read_json(state_file)
    state["plan_hash"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    if agent_id in state.get("tasks", {}):
        state["tasks"][agent_id]["status"] = "pending"
        state["tasks"][agent_id]["error"] = None
        state["tasks"][agent_id]["activity"] = f"Modelo alterado para {target_provider} ({target_model or 'padrão'}). Pronto para executar."
        state["tasks"][agent_id].pop("reason", None)
    state["status"] = "ready"
    state.pop("exhausted_provider", None)
    state.pop("suggested_provider", None)
    state.pop("pid", None)
    write_json(state_file, state)

    store = Store(run)
    store.event(agent_id, "status", f"Modelo alterado para {target_provider} ({target_model or 'padrão'}). Retomando...")
    store.event("coordinator", "status", f"Agente {agent_id} migrado para {target_provider}. Retomando execução.")

    cmd = [sys.executable, "-m", "agentes", "resume", str(run),
           "--workers", "3", "--codex-workers", "1", "--gemini-workers", "1", "--opencode-workers", "2", "--demo-delay", "1"]
    with (run / "coordinator.log").open("a") as log:
        subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent.parent,
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)


def pause_or_resume_agent(run, agent_id):
    run = Path(run).resolve()
    state_file = run / "state.json"
    state = read_json(state_file)
    task_state = state.get("tasks", {}).get(agent_id, {})
    curr_status = task_state.get("status")

    if curr_status == "running":
        pid = state.get("pid")
        if isinstance(pid, int) and pid > 1:
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(25):
                    time.sleep(0.1)
                    try:
                        with run_lock(run):
                            break
                    except RuntimeError:
                        pass
            except OSError:
                pass
        state = read_json(state_file)
        state["tasks"][agent_id]["status"] = "paused"
        state["tasks"][agent_id]["activity"] = "Pausado manualmente pelo usuário"
        state["status"] = "paused"
        state.pop("pid", None)
        write_json(state_file, state)
        Store(run).event(agent_id, "status", "Agente pausado manualmente pelo usuário.")
        return "paused"
    elif curr_status in ("paused", "interrupted", "failed"):
        state["tasks"][agent_id]["status"] = "pending"
        state["tasks"][agent_id]["activity"] = "Retomando execução..."
        state["status"] = "ready"
        write_json(state_file, state)
        Store(run).event(agent_id, "status", "Agente retomado pelo usuário.")
        cmd = [sys.executable, "-m", "agentes", "resume", str(run),
               "--workers", "3", "--codex-workers", "1", "--gemini-workers", "1", "--opencode-workers", "2", "--demo-delay", "1"]
        with (run / "coordinator.log").open("a") as log:
            subprocess.Popen(cmd, cwd=Path(__file__).resolve().parent.parent,
                             stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        return "resumed"
    return None


def confirm_keep_paused(run, agent_id):
    run = Path(run).resolve()
    state = read_json(run / "state.json")
    if agent_id in state.get("tasks", {}):
        state["tasks"][agent_id]["activity"] = "Pausado (aguardando liberação da cota de assinatura)"
        write_json(run / "state.json", state)
        Store(run).event(agent_id, "status", "Execução mantida pausada conforme escolha do usuário.")


def help_screen():
    def _screen(stdscr):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        help_lines = [
            "╔══════════════════════════════════════════════════════════════════════════════════════════╗",
            "║                              📖 ATALHOS E GUIA DO SISTEMA                                ║",
            "╠══════════════════════════════════════════════════════════════════════════════════════════╣",
            "║                                                                                          ║",
            "║  NAVEGAÇÃO ENTRE AGENTES E JANELAS                                                       ║",
            "║    • Alt + Setas (← ↑ → ↓)    Mudar foco entre os painéis dos agentes                    ║",
            "║    • Ctrl + ←  /  Ctrl + →    Trocar de página de agentes                                ║",
            "║    • Ctrl + R                 Resetar e reorganizar janelas (restaura o Coordenador)     ║",
            "║                                                                                          ║",
            "║  CONTROLE DO AGENTE SELECIONADO                                                          ║",
            "║    • t                        Trocar o modelo deste agente (mesmo durante a execução)    ║",
            "║    • p                        Pausar ou despausar a execução deste agente                ║",
            "║    • c                        Copiar todo o histórico do agente para a área de transf.   ║",
            "║    • o                        Abrir a pasta de entregas / artefatos no explorador        ║",
            "║    • ↑ / ↓  ou  j / k         Rolar o histórico do agente linha a linha                  ║",
            "║    • PgUp / PgDn  ou  Espaço  Rolar o histórico página inteira                           ║",
            "║    • Home / End               Ir direto ao topo ou final do histórico                    ║",
            "║                                                                                          ║",
            "║  COORDENADOR & SESSÃO                                                                    ║",
            "║    • r                        Revisar entregas com o Coordenador (ao finalizar)          ║",
            "║    • m                        Alternar mouse (clique em botões vs selecionar texto)      ║",
            "║    • Ctrl + B depois D        Sair do painel (mantém agentes rodando em 2º plano)        ║",
            "║    • F1  ou  ?                Abrir esta tela de ajuda                                   ║",
            "║    • q  ou  Esc               Fechar esta ajuda e voltar às janelas dos agentes          ║",
            "║                                                                                          ║",
            "║          Pressione qualquer tecla (q, Esc, Enter, F1) para fechar esta janela            ║",
            "╚══════════════════════════════════════════════════════════════════════════════════════════╝"
        ]
        height, width = stdscr.getmaxyx()
        start_row = max(0, (height - len(help_lines)) // 2)
        stdscr.erase()
        for i, line in enumerate(help_lines):
            row = start_row + i
            if row < height:
                attr = curses.A_BOLD if i in (1, 4, 10, 19, 25) else curses.A_NORMAL
                col = max(0, (width - len(line)) // 2)
                try:
                    stdscr.addstr(row, col, line[:max(0, width - 1)], attr)
                except curses.error:
                    pass
        stdscr.refresh()
        while True:
            k = stdscr.getch()
            if k != -1:
                break
    try:
        curses.wrapper(_screen)
    except Exception:
        pass


def open_help_window(run=None):
    """Open help in a brand new tmux window, preserving all existing agent panes untouched."""
    if shutil.which("tmux"):
        repo = Path(__file__).resolve().parent.parent
        help_cmd = f"cd {shlex.quote(str(repo))} && {sys.executable} -m agentes help"
        target = []
        if run:
            sess_name = "agentes-" + re.sub(r"[^a-zA-Z0-9_-]", "-", Path(run).name)
            target = ["-t", f"{sess_name}:"]
        res = subprocess.run(["tmux", "new-window", *target, "-n", "ajuda", help_cmd],
                             capture_output=True, text=True)
        if res.returncode == 0:
            return
    help_screen()


def change_model_in_curses(stdscr, run, agent):
    run = Path(run).resolve()
    plan = read_json(run / "plan.json")
    task = next((t for t in plan["tasks"] if t["id"] == agent), None)
    if not task:
        return False
    from .wizard import model_catalogs, provider_name_for
    catalogs = model_catalogs()
    candidates = []
    other_runners = [r for r in ('gemini', 'opencode', 'codex', 'claude') if r != task['runner']]
    for runner in other_runners + [task['runner']]:
        for m in catalogs.get(runner, []):
            if runner == task['runner'] and m['id'] == task.get('model'):
                continue
            candidates.append({**m, "runner": runner})
    if not candidates:
        target_provider = "gemini" if task["runner"] == "codex" else "codex"
        default_model = "gemini-3.8-flash-high" if target_provider == "gemini" else "gpt-5.6-sol"
        candidates = [{"id": default_model, "description": "Modelo padrão", "runner": target_provider}]

    selected_index = 0
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        page_capacity = max(4, height - 9)
        top_offset = max(0, min(selected_index - page_capacity // 2, len(candidates) - page_capacity))
        visible_candidates = candidates[top_offset:top_offset + page_capacity]

        dialog_lines = [
            "╔══════════════════════════════════════════════════════════════════════════════════════════╗",
            f"  🔄 TROCAR MODELO DO AGENTE: {task['role']} ({agent})",
            f"  Atual: {provider_name_for(task['runner'])} • {task.get('model', 'padrão')}",
            "╟──────────────────────────────────────────────────────────────────────────────────────────╢",
            f"  Escolha o novo modelo ({selected_index + 1}/{len(candidates)} — use ↑/↓ e Enter, ou número correspondente):",
            ""
        ]
        for v_idx, m in enumerate(visible_candidates):
            actual_idx = top_offset + v_idx
            cursor = "👉 " if actual_idx == selected_index else "   "
            flash = " ⚡ Flash" if "-flash-" in m['id'] else ""
            pro = " 🧠 Pro" if "-pro" in m['id'] else ""
            free = " 🎁 Grátis" if m.get("is_free") else ""
            desc = " ".join(clean(m.get('description', '')).split())[:30]
            detail = f" — {desc}" if desc else ""
            pname = provider_name_for(m.get('runner', task['runner']))
            dialog_lines.append(f"  {cursor}[{actual_idx + 1}] {pname} • {m['id']}{flash}{pro}{free}{detail}")

        dialog_lines.extend([
            "",
            "  [Enter] Confirmar modelo selecionado   [↑/↓] Rolar opções   [ESC / q] Cancelar",
            "╚══════════════════════════════════════════════════════════════════════════════════════════╝"
        ])
        for r, l in enumerate(dialog_lines):
            if r < height - 1:
                attr = curses.A_BOLD if r in (1, 3, 5) else curses.A_NORMAL
                if "👉" in l:
                    attr = curses.A_STANDOUT | curses.A_BOLD
                try:
                    stdscr.addstr(r, 1, l[:max(0, width - 2)], attr)
                except curses.error:
                    pass
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (27, ord('q'), ord('Q')):
            return False
        elif ch in (curses.KEY_UP, ord('k')):
            selected_index = max(0, selected_index - 1)
        elif ch in (curses.KEY_DOWN, ord('j')):
            selected_index = min(len(candidates) - 1, selected_index + 1)
        elif ord('1') <= ch <= ord('9') and (ch - ord('1')) < len(candidates):
            selected_index = ch - ord('1')
            chosen_item = candidates[selected_index]
            switch_agent_and_resume(run, agent, chosen_item.get("runner", "gemini"), chosen_item["id"])
            return True
        elif ch in (10, 13, curses.KEY_ENTER):
            chosen_item = candidates[selected_index]
            switch_agent_and_resume(run, agent, chosen_item.get("runner", "gemini"), chosen_item["id"])
            return True


def cells(text):
    return sum(0 if unicodedata.combining(c) or c in "\ufe0f\u200d" else
               2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def wrap_lines(lines, width):
    """Wrap by terminal cells, including wide emoji; preserve every word."""
    width = max(4, width)
    result = []
    for line in lines:
        for paragraph in clean(line).expandtabs(4).split("\n"):
            current = ""
            for word in paragraph.split(" "):
                candidate = current + (" " if current else "") + word
                if cells(candidate) <= width:
                    current = candidate
                    continue
                if current:
                    result.append(current)
                    current = ""
                for char in word:
                    if cells(current + char) > width:
                        result.append(current)
                        current = ""
                    current += char
            result.append(current)
    return result


def comfortable_page_size(width, height):
    """Reserve readable pane dimensions and never place over four agents on a page."""
    pane_columns, pane_rows = 58, 15
    total_panes = max(2, (max(width, pane_columns) // pane_columns) *
                      (max(height, pane_rows) // pane_rows))
    return max(1, min(4, total_panes - 1))


def agent_pages(tasks, page_size):
    return [tasks[index:index + page_size] for index in range(0, len(tasks), page_size)]


def completion_banner(state, total, complete, run):
    if state['status'] == 'succeeded':
        headline = "✅ FINALIZADO SEM ERROS"
        detail = f"Todos os {total} agentes concluíram e suas entregas foram validadas."
    elif state['status'] == 'failed':
        failed = sum(item['status'] == 'failed' for item in state['tasks'].values())
        blocked = sum(item['status'] == 'blocked' for item in state['tasks'].values())
        headline = "❌ FINALIZADO COM ERROS"
        detail = f"{complete}/{total} concluídos • {failed} falharam • {blocked} bloqueados."
    else:
        return []
    return ["╔══════════════════════════════════════╗", headline, detail,
            "[ 📂 ABRIR PASTA DE ENTREGAS ]", "Clique no botão ou pressione a tecla o.",
            artifacts_uri(run), "╚══════════════════════════════════════╝", ""]


def render(run, agent=None, width=100, height=35):
    # height is retained for callers; scrolling belongs to the viewport, not the content.
    run = Path(run)
    plan, state = read_json(run / "plan.json"), read_json(run / "state.json")
    if agent:
        task = next((t for t in plan["tasks"] if t["id"] == agent), None)
        if not task:
            raise ValueError(f"Agente inexistente: {agent}")
        s = state["tasks"][agent]
        elapsed = max(0, int(s.get("finished_at", time.time()) - s.get("started_at", time.time())))
        provider = provider_name(task)
        waiting = [d for d in task['depends_on'] if state['tasks'][d]['status'] != 'succeeded']
        lines = [f"👤 {task['role']} ({agent})", status(s['status']),
                 f"Provedor: {provider}", f"Modelo: {model_name(task)}",
                 f"Tentativa {s['attempts']} • Tempo: {elapsed}s", ""]
        if s['status'] == 'succeeded':
            lines.append("Entrega pronta e validada.")
        elif waiting:
            lines.append("Aguardando entrega de: " + ", ".join(waiting))
        else:
            lines.append("Agora: " + brief(s.get('activity', 'Aguardando início')))
        if s.get('error'):
            err = s['error']
            if any(w in err.lower() for w in ('usage limit', 'quota', 'rate limit', '429')):
                sug = 'Google Antigravity (Gemini)' if task['runner'] == 'codex' else 'Codex'
                lines += [
                    "",
                    "╔══════════════════════════════════════════════════════════════╗",
                    "⏸ LIMITE DE USO DA ASSINATURA ATINGIDO",
                    f"A cota de {provider} esgotou para esta tarefa.",
                    f"💡 Sugestão recomendada: migrar este agente para {sug}.",
                    "",
                    "[ 🔄 TROCAR MODELO (tecla t) ]   [ ⏸ MANTER PAUSADO (tecla p) ]",
                    "Clique no botão desejado ou pressione a tecla t ou p.",
                    "╚══════════════════════════════════════════════════════════════╝",
                    "",
                    "Detalhes do erro:",
                    brief(err, 240)
                ]
            else:
                lines += ["", "❌ O que precisa de atenção", brief(err, 240),
                          "Detalhes completos estão no histórico técnico."]
        summary = task_summary(task)
        lines += ["", "🎯 O que este agente fará", summary, f"Etapa: {task['stage']}",
                  "", "📦 Arquivos da entrega", *task['outputs'], "", "🕒 Últimas atividades (mais novas primeiro)"]
        lines += list(reversed(recent_events(run, agent, 30))) or ["Ainda não há atividade."]
        lines += ["", "📂 Abrir todas as entregas (Ctrl+clique):", artifacts_uri(run),
                  "Pasta deste agente:", str(run / 'artifacts' / agent),
                  "Histórico técnico completo:", str(run / 'logs')]
    else:
        complete = sum(s['status'] == 'succeeded' for s in state['tasks'].values())
        total = len(plan['tasks'])
        lines = completion_banner(state, total, complete, run)
        team_sel = read_json(run / 'team-selection.json') if (run / 'team-selection.json').exists() else {}
        coord = team_sel.get('coordinator', {})
        coord_prov = 'Google Antigravity' if coord.get('runner') == 'gemini' else ('OpenCode' if coord.get('runner') == 'opencode' else ('Simulação' if coord.get('runner') == 'demo' else 'Codex'))
        coord_mod = coord.get('model') or ('gemini-3.1-pro-high' if coord.get('runner') == 'gemini' else 'gpt-6-astra')
        total_rounds = team_sel.get('total_rounds', 1)
        cyclic_info = f" • 🔄 {total_rounds} rodadas cíclicas" if total_rounds > 1 else ""
        lines += [
            "🧭 Coordenador",
            f"Provedor: {coord_prov} • Modelo: {coord_mod}",
            f"👥 Equipe: {total} tarefas{cyclic_info}", 
            f"{status(state['status'])} • {complete}/{total} entregas validadas",
            "Para trocar de página: Ctrl+← ou Ctrl+→."
        ]
        if state['status'] == 'succeeded':
            lines += [
                "",
                "[ 💬 REVISAR COM COORDENADOR (tecla r) ]",
                "Fale com o coordenador para avaliar os resultados ou pedir nova rodada."
            ]
        if state['status'] == 'paused':
            exhausted = state.get('exhausted_provider')
            for task_state in state['tasks'].values():
                if not exhausted and task_state.get('status') == 'paused' and task_state.get('error'):
                    err = task_state['error'].lower()
                    if any(w in err for w in ('usage limit', 'quota', 'rate limit', '429')):
                        exhausted = 'Codex' if ('codex' in err or 'chatgpt' in err) else 'Google Antigravity'
                        break
            if exhausted:
                lines += [
                    "╔══════════════════════════════════════════════════════════════╗",
                    "⏸ LIMITE DE USO ATINGIDO NA ASSINATURA",
                    f"O provedor {exhausted} esgotou o limite de cota.",
                    "💡 Sugestão recomendada: Google Antigravity (Gemini).",
                    "Acesse a tela do agente pausado para clicar em:",
                    "[ 🔄 TROCAR MODELO (tecla t) ] ou [ ⏸ MANTER PAUSADO (tecla p) ]",
                    "╚══════════════════════════════════════════════════════════════╝"
                ]
            else:
                lines += ["Execução pausada. Confira o agente que precisa de atenção."]
        lines += ["", "🎯 Objetivo", plan['objective'], "", "📋 Plano e progresso"]
        for stage in dict.fromkeys(t['stage'] for t in plan['tasks']):
            lines.append(stage)
            for task in plan['tasks']:
                if task['stage'] == stage:
                    s = state['tasks'][task['id']]
                    lines.append(f"{status(s['status'])} — {task['role']} ({task['id']})")
                    lines.append(f"    {provider_name(task)} • modelo {model_name(task)}")
                    lines.append(f"    🎯 {task_summary(task)}")
                    waiting = [d for d in task['depends_on'] if state['tasks'][d]['status'] != 'succeeded']
                    if waiting:
                        lines.append("    Aguarda: " + ", ".join(waiting))
            lines.append("")
        lines += ["📂 Abrir entregas (Ctrl+clique ou pressione o):", artifacts_uri(run),
                  "Pasta local:", str(run / 'artifacts'),
                  "", "Fechar este painel não interrompe os agentes.",
                  f"Para interromper: python3 -m agentes stop {run}"]
    return "\n".join(wrap_lines(lines, width))


def watch(run, agent=None, once=False):
    if once or not sys.stdout.isatty() or not sys.stdin.isatty():
        print(render(run, agent, shutil.get_terminal_size((100, 35)).columns))
        return

    def screen(stdscr):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        mouse_enabled = True
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
        except curses.error:
            pass
        stdscr.timeout(500)
        stdscr.keypad(True)
        offset = 0
        completion_announced = False
        feedback_msg = None
        feedback_until = 0
        current_state = None
        while True:
            height, width = stdscr.getmaxyx()
            state_file = Path(run) / "state.json"
            if state_file.exists():
                try:
                    current_state = read_json(state_file).get("status")
                except Exception:
                    pass
            content = render(run, agent, max(4, width - 1)).splitlines()
            available = max(1, height - 2)
            offset = min(offset, max(0, len(content) - available))
            stdscr.erase()
            for row, line in enumerate(content[offset:offset + available]):
                try:
                    if "FINALIZADO SEM ERROS" in line or "FINALIZADO COM ERROS" in line:
                        attribute = curses.A_REVERSE | curses.A_BOLD
                    elif "ABRIR PASTA DE ENTREGAS" in line:
                        attribute = curses.A_STANDOUT | curses.A_BOLD
                    elif "TROCAR MODELO" in line:
                        attribute = curses.A_STANDOUT | curses.A_BOLD
                    elif "MANTER PAUSADO" in line:
                        attribute = curses.A_REVERSE | curses.A_BOLD
                    else:
                        attribute = curses.A_BOLD if offset + row < 2 else curses.A_NORMAL
                    stdscr.addstr(row, 0, line, attribute)
                except curses.error:
                    pass
            if feedback_msg and time.time() < feedback_until:
                footer = f"👉 {feedback_msg}"
            else:
                actions = ""
                if agent:
                    task_st = read_json(Path(run) / "state.json").get("tasks", {}).get(agent, {}).get("status")
                    if task_st != "succeeded":
                        actions = " • t trocar modelo • p pausar"
                elif agent is None and current_state == "succeeded":
                    actions = " • r revisar com coordenador"
                footer = f"Alt+setas foco • ↑↓ rolar • c copiar • m mouse • o abrir • F1 ajuda • q sair{actions} | {offset+1}–{min(offset+available,len(content))}/{len(content)}"
            for row, text in ((height-2, '─' * max(0,width-1)), (height-1, footer)):
                try:
                    stdscr.addstr(row, 0, wrap_lines([text], max(4,width-1))[0], curses.A_DIM)
                except curses.error:
                    pass
            stdscr.refresh()
            if current_state in ("succeeded", "failed") and not completion_announced and agent is None:
                offset = 0
                curses.beep()
                curses.flash()
                completion_announced = True
            key = stdscr.getch()
            if key in (curses.KEY_F1, ord('?'), ord('h'), ord('H')):
                open_help_window(run)
            elif key in (ord('q'), 27):
                break
            if key in (curses.KEY_DOWN, ord('j')):
                offset += 1
            elif key in (curses.KEY_UP, ord('k')):
                offset = max(0, offset - 1)
            elif key in (curses.KEY_NPAGE, ord(' ')):
                offset += available
            elif key == curses.KEY_PPAGE:
                offset = max(0, offset - available)
            elif key == curses.KEY_HOME:
                offset = 0
            elif key == curses.KEY_END:
                offset = max(0, len(content) - available)
            elif key in (ord('o'), ord('O')):
                open_artifacts(run)
            elif key in (ord('c'), ord('C')):
                copy_to_clipboard("\n".join(content))
                feedback_msg = "📋 Todo o texto deste agente foi copiado para a área de transferência!"
                feedback_until = time.time() + 4
            elif key in (ord('m'), ord('M')):
                mouse_enabled = not mouse_enabled
                try:
                    curses.mousemask(curses.ALL_MOUSE_EVENTS if mouse_enabled else 0)
                except curses.error:
                    pass
                if mouse_enabled:
                    feedback_msg = "🖱️ Mouse ativado para clique em botões."
                else:
                    feedback_msg = "🖱️ Mouse desativado (arraste normal seleciona texto direto)."
                feedback_until = time.time() + 4
            elif key in (ord('t'), ord('T')) and agent:
                task_st = read_json(Path(run) / "state.json").get("tasks", {}).get(agent, {}).get("status")
                if task_st != "succeeded":
                    if change_model_in_curses(stdscr, run, agent):
                        feedback_msg = "✅ Modelo alterado! Agente retomado com sucesso."
                        feedback_until = time.time() + 4
            elif key in (ord('p'), ord('P')) and agent:
                res = pause_or_resume_agent(run, agent)
                if res == "paused":
                    feedback_msg = "⏸ Agente pausado pelo usuário."
                elif res == "resumed":
                    feedback_msg = "▶ Agente retomado pelo usuário."
                else:
                    feedback_msg = "⏸ Nenhuma alteração de pausa."
                feedback_until = time.time() + 4
            elif key in (ord('r'), ord('R')) and agent is None:
                if current_state == "succeeded":
                    curses.def_prog_mode()
                    curses.endwin()
                    try:
                        from .review import review_with_coordinator
                        new_run = review_with_coordinator(run)
                        if new_run:
                            break
                    finally:
                        curses.reset_prog_mode()
            elif key == curses.KEY_MOUSE and mouse_enabled:
                try:
                    _, mouse_x, mouse_y, _, _ = curses.getmouse()
                    content_row = offset + mouse_y
                    if content_row < len(content):
                        line_text = content[content_row]
                        if "ABRIR PASTA DE ENTREGAS" in line_text:
                            open_artifacts(run)
                        elif "REVISAR COM COORDENADOR" in line_text and agent is None:
                            curses.def_prog_mode()
                            curses.endwin()
                            try:
                                from .review import review_with_coordinator
                                new_run = review_with_coordinator(run)
                                if new_run:
                                    break
                            finally:
                                curses.reset_prog_mode()
                        elif "TROCAR MODELO" in line_text and agent:
                            if change_model_in_curses(stdscr, run, agent):
                                feedback_msg = "✅ Modelo alterado! Agente retomado com sucesso."
                                feedback_until = time.time() + 4
                        elif ("MANTER PAUSADO" in line_text or "PAUSAR" in line_text) and agent:
                            res = pause_or_resume_agent(run, agent)
                            if res == "paused":
                                feedback_msg = "⏸ Agente pausado pelo usuário."
                            elif res == "resumed":
                                feedback_msg = "▶ Agente retomado pelo usuário."
                            feedback_until = time.time() + 4
                        elif "AJUDA" in line_text:
                            open_help_window(run)
                except curses.error:
                    pass
    try:
        curses.wrapper(screen)
    except KeyboardInterrupt:
        pass


def rebuild_dashboard_windows(run):
    """Rebuild all dashboard windows and panes in-place without detaching the tmux client."""
    if not shutil.which("tmux"):
        return
    run = Path(run).resolve()
    plan = read_json(run / "plan.json")
    name = "agentes-" + re.sub(r"[^a-zA-Z0-9_-]", "-", run.name)

    def tmux(*args, check=True):
        result = subprocess.run(["tmux", *args], text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError("tmux: " + result.stderr.strip())
        return result

    def view_command(agent=None):
        args = [sys.executable, "-m", "agentes", "watch", str(run)]
        if agent:
            args += ["--agent", agent]
        return "cd " + shlex.quote(str(Path(__file__).resolve().parent.parent)) + " && " + shlex.join(args)

    if tmux("has-session", "-t", name, check=False).returncode != 0:
        dashboard(run, attach=False)
        return

    client_size = tmux("display-message", "-t", name, "-p", "#{client_width} #{client_height}", check=False).stdout.strip().split()
    if len(client_size) == 2 and client_size[0].isdigit() and int(client_size[0]) > 0:
        width, height = int(client_size[0]), int(client_size[1])
    else:
        terminal = shutil.get_terminal_size((180, 50))
        width, height = max(80, terminal.columns), max(24, terminal.lines)
    page_size = comfortable_page_size(width, height)
    pages = agent_pages(plan["tasks"], page_size)

    # Create temporary holding window so session stays alive when closing old windows
    tmux("new-window", "-d", "-t", f"{name}:99", "-n", "rebuilding", view_command(), check=False)

    # Kill old windows except 99
    existing_windows = tmux("list-windows", "-t", name, "-F", "#{window_index}", check=False).stdout.splitlines()
    for w in existing_windows:
        if w != "99":
            tmux("kill-window", "-t", f"{name}:{w}", check=False)

    # Recreate windows and panes
    for page_index, tasks in enumerate(pages):
        target = f"{name}:{page_index}"
        tmux("new-window", "-t", target, "-n", f"pagina-{page_index + 1}", view_command())
        tmux("set-option", "-w", "-t", target, "pane-border-status", "top")
        tmux("set-option", "-w", "-t", target, "pane-border-format", " #{pane_title} ")
        tmux("select-pane", "-t", f"{target}.0", "-T", "Coordenador")
        for task in tasks:
            pane = tmux("split-window", "-P", "-F", "#{pane_id}", "-t", target, view_command(task["id"])).stdout.strip()
            tmux("select-pane", "-t", pane, "-T", task["id"])
            tmux("select-layout", "-t", target, "tiled")
        tmux("select-pane", "-t", f"{target}.0")

    help_cmd = f"cd {shlex.quote(str(Path(__file__).resolve().parent.parent))} && {sys.executable} -m agentes help"
    condition = "#{m:agentes-*,#{session_name}}"
    tmux("bind-key", "-n", "F1", "if-shell", "-F", condition,
         f"new-window -t {name}: -n 'ajuda' {shlex.quote(help_cmd)}", "send-keys F1")
    # Kill temporary holding window and focus coordinator
    tmux("kill-window", "-t", f"{name}:99", check=False)
    tmux("select-window", "-t", f"{name}:0")
    tmux("select-pane", "-t", f"{name}:0.0")


def dashboard(run, attach=True):
    if not shutil.which("tmux"):
        raise RuntimeError("tmux não instalado. Use 'python3 -m agentes watch CAMINHO' ou instale tmux.")
    run = Path(run).resolve()
    plan = read_json(run / "plan.json")
    name = "agentes-" + re.sub(r"[^a-zA-Z0-9_-]", "-", run.name)
    def tmux(*args, check=True):
        result = subprocess.run(["tmux", *args], text=True, capture_output=True)
        if check and result.returncode:
            raise RuntimeError("tmux: " + result.stderr.strip())
        return result
    def view_command(agent=None):
        args = [sys.executable, "-m", "agentes", "watch", str(run)]
        if agent:
            args += ["--agent", agent]
        # tmux invokes a shell; quote every argument, including repository paths.
        return "cd " + shlex.quote(str(Path(__file__).resolve().parent.parent)) + " && " + shlex.join(args)
    exists = tmux("has-session", "-t", name, check=False).returncode == 0
    if exists:
        layout = tmux("show-options", "-t", name, "-qv", "@agentes_layout", check=False).stdout.strip()
        pane_titles = tmux("list-panes", "-t", f"{name}:0", "-F", "#{pane_title}", check=False).stdout.splitlines()
        if layout != "5" or not pane_titles or pane_titles[0] != "Coordenador":
            # A sessão perdeu o coordenador ou mudou de layout; reconstruir
            rebuild_dashboard_windows(run)
            exists = True
    if not exists:
        terminal = shutil.get_terminal_size((180, 50))
        width, height = max(80, terminal.columns), max(24, terminal.lines)
        page_size = comfortable_page_size(width, height)
        pages = agent_pages(plan["tasks"], page_size)
        tmux("new-session", "-d", "-s", name, "-n", "pagina-1", "-x", str(width), "-y", str(height), view_command())
        tmux("set-option", "-t", name, "@agentes_layout", "5")
        tmux("set-option", "-t", name, "@agentes_page_size", str(page_size))
        tmux("set-option", "-t", name, "status-left", " #[bold]Agentes #[default]")
        tmux("set-option", "-t", name, "status-right", " Alt+setas foco • Ctrl+←/→ páginas • Ctrl+R resetar • F1 ajuda | %H:%M ")
        tmux("set-option", "-t", name, "mouse", "on")
        tmux("set-option", "-s", "set-clipboard", "on", check=False)
        tmux("set-option", "-t", name, "window-status-format", " Página #I ")
        tmux("set-option", "-t", name, "window-status-current-format", "#[reverse,bold] Página #I #[default]")
        tmux("set-option", "-t", name, "pane-border-status", "top")
        tmux("set-option", "-t", name, "pane-border-format", " #{pane_title} ")
        for page_index, tasks in enumerate(pages):
            target = f"{name}:{page_index}"
            if page_index:
                tmux("new-window", "-t", target, "-n", f"pagina-{page_index + 1}", view_command())
            tmux("set-option", "-w", "-t", target, "pane-border-status", "top")
            tmux("set-option", "-w", "-t", target, "pane-border-format", " #{pane_title} ")
            tmux("select-pane", "-t", f"{target}.0", "-T", "Coordenador")
            for task in tasks:
                pane = tmux("split-window", "-P", "-F", "#{pane_id}", "-t", target,
                            view_command(task["id"])).stdout.strip()
                tmux("select-pane", "-t", pane, "-T", task["id"])
                tmux("select-layout", "-t", target, "tiled")
            tmux("select-pane", "-t", f"{target}.0")
        condition = "#{m:agentes-*,#{session_name}}"
        tmux("bind-key", "-n", "C-Right", "if-shell", "-F", condition,
             "next-window", "send-keys C-Right")
        tmux("bind-key", "-n", "C-Left", "if-shell", "-F", condition,
             "previous-window", "send-keys C-Left")
        for key, direction in (("M-Up", "-U"), ("M-Down", "-D"),
                               ("M-Left", "-L"), ("M-Right", "-R")):
            tmux("bind-key", "-n", key, "if-shell", "-F", condition,
                 f"select-pane {direction}", f"send-keys {key}")
        reset_cmd = f"python3 -m agentes dashboard {shlex.quote(str(run))} --rebuild"
        tmux("bind-key", "-n", "C-r", "if-shell", "-F", condition,
             f"run-shell 'cd {shlex.quote(str(Path(__file__).resolve().parent.parent))} && {reset_cmd}'",
             "send-keys C-r")
        help_cmd = f"cd {shlex.quote(str(Path(__file__).resolve().parent.parent))} && {sys.executable} -m agentes help"
        tmux("bind-key", "-n", "F1", "if-shell", "-F", condition,
             f"new-window -t {name}: -n 'ajuda' {shlex.quote(help_cmd)}", "send-keys F1")
        default_wheel_up = ('if-shell -F "#{||:#{pane_in_mode},#{mouse_any_flag}}" '
                            '"send-keys -M" "copy-mode -e"')
        tmux("bind-key", "-n", "WheelUpPane", "if-shell", "-F", condition,
             "select-pane -t = ; send-keys -t = Up", default_wheel_up)
        tmux("bind-key", "-n", "WheelDownPane", "if-shell", "-F", condition,
             "select-pane -t = ; send-keys -t = Down", "send-keys -M")
        tmux("select-window", "-t", f"{name}:0")
    print(f"Painéis: tmux attach -t {name}", flush=True)
    if attach and sys.stdin.isatty():
        subprocess.run(["tmux", "switch-client" if os.environ.get("TMUX") else "attach-session", "-t", name], check=True)
    return name
