"""Guided terminal entry point: no manifest paths or shell commands required."""
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .engine import create_run, run_engine
from .manifest import validate, load
from .monitor import clean
from .storage import read_json, write_json
from .runners import API_ENV


def ask(text, default=None):
    from .ui import format_prompt
    value = input(format_prompt(text, default)).strip()
    return value or default



def agent_count():
    while True:
        value = ask("👥 Quantos agentes executores? (1 a 8; coordenador separado)", "3")
        try:
            count = int(value)
            if 1 <= count <= 8:
                return count
        except ValueError:
            pass
        print("Informe um número de 1 a 8.")


def choose_inputs():
    """Use a graphical picker when available; fall back to one readable path."""
    if ask("📎 Deseja anexar arquivo(s) para os agentes? (s/n)", "n").lower() not in ('s', 'sim'):
        return []
    paths = []
    import platform
    if platform.system() == 'Darwin' and shutil.which('osascript'):
        script = ('try\n'
                  '  set chosen to choose file with prompt "Escolha os arquivos da atividade" with multiple selections allowed\n'
                  '  set posixList to {}\n'
                  '  repeat with aFile in chosen\n'
                  '    set end of posixList to POSIX path of aFile\n'
                  '  end repeat\n'
                  '  set AppleScript\'s text item delimiters to ASCII character 10\n'
                  '  return posixList as text\n'
                  'on error\n'
                  '  return ""\n'
                  'end try')
        res = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, check=False)
        selected = [Path(line) for line in res.stdout.splitlines() if line.strip()]
        for p in selected:
            if str(p.resolve()) not in {str(item.resolve()) for item in paths}:
                paths.append(p)
        if paths:
            print(f"📎 Total selecionado: {len(paths)} arquivo(s) — " + ', '.join(p.name for p in paths))
            return paths
    if shutil.which('zenity') and os.environ.get('DISPLAY'):
        while True:
            print("Selecione um ou mais arquivos desta pasta usando Ctrl ou Shift.")
            result = subprocess.run(['zenity', '--file-selection', '--multiple', '--separator=\n',
                                     '--title=Escolha os arquivos da atividade'],
                                    capture_output=True, text=True, check=False)
            selected = [Path(line) for line in result.stdout.splitlines() if line.strip()]
            known = {str(path.expanduser().resolve()) for path in paths}
            for path in selected:
                key = str(path.expanduser().resolve())
                if key not in known:
                    paths.append(path)
                    known.add(key)
            if selected:
                print(f"📎 Total selecionado: {len(paths)} arquivo(s) — " + ', '.join(p.name for p in paths))
            else:
                print("Nenhum arquivo foi selecionado nesta pasta.")
            if ask("Adicionar arquivos de outra pasta? (s/n)", "n").lower() not in ('s', 'sim'):
                return paths
    while True:
        value = ask("Caminho completo do arquivo (Enter conclui)", "")
        if value:
            candidate = Path(value).expanduser()
            if str(candidate.resolve()) not in {str(path.resolve()) for path in paths}:
                paths.append(candidate)
        if not value or ask("Adicionar arquivo de outra pasta? (s/n)", "n").lower() not in ('s', 'sim'):
            return paths


def codex_catalog():
    """Only public model metadata, never authentication files. Cache is not entitlement proof."""
    path = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))) / 'models_cache.json'
    try:
        data = read_json(path)
        return [{"id": m['slug'], "description": m.get('description', '')}
                for m in data.get('models', []) if m.get('visibility') == 'list'
                and isinstance(m.get('slug'), str)]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def antigravity_catalog():
    """Ask the official CLI for visible Gemini models; do not inspect credentials."""
    if not shutil.which('agy'):
        return []
    try:
        result = subprocess.run(['agy', 'models'], capture_output=True, text=True,
                                timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    models = []
    for line in result.stdout.splitlines():
        parts = line.strip().split('\t', 1)
        if len(parts) == 2 and parts[0].startswith('gemini-'):
            models.append({'id': parts[0], 'description': parts[1]})
    return models


def opencode_catalog():
    """List models available in the OpenCode CLI, tagging free and community options."""
    from .runners import opencode_bin
    bin_path = opencode_bin()
    if not Path(bin_path).exists() and not shutil.which(bin_path):
        return []
    try:
        result = subprocess.run([bin_path, 'models'], capture_output=True, text=True,
                                timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    models = []
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        is_free = "-free" in name or name.startswith("opencode/")
        tag = " 🎁 Grátis / Rotação Livre" if is_free else ""
        models.append({"id": name, "description": f"Modelo OpenCode{tag}", "is_free": is_free})
    return models


def claude_catalog():
    """List Anthropic Claude models available via Claude Code CLI."""
    if not shutil.which('claude'):
        return []
    return [
        {"id": "claude-3-7-sonnet", "description": "Claude 3.7 Sonnet (Híbrido / Raciocínio)"},
        {"id": "claude-3-5-sonnet", "description": "Claude 3.5 Sonnet (Padrão para código)"},
        {"id": "claude-3-5-haiku", "description": "Claude 3.5 Haiku (Rápido e leve)"},
        {"id": "claude-opus", "description": "Claude Opus (Tarefas complexas)"},
    ]


def model_catalogs():
    catalogs = {'codex': codex_catalog(), 'gemini': antigravity_catalog()}
    oc = opencode_catalog()
    if oc:
        catalogs['opencode'] = oc
    cl = claude_catalog()
    if cl:
        catalogs['claude'] = cl
    return catalogs


def planner_model(candidates):
    ids = [m['id'] for m in candidates]
    for preferred in ('gpt-6-astra', 'gpt-5.6-sol', 'gpt-5.6-terra'):
        if preferred in ids:
            return preferred
    return ids[0] if ids else None


def edit_models(plan, catalogs, ask_fn=None):
    """Let the user revise coordinator choices without editing JSON."""
    ask_input = ask_fn or ask
    while True:
        selected = ask_input("🔧 Agente para trocar o modelo (número; Enter continua)", "")
        if not selected:
            providers = {task['runner'] for task in plan['tasks']}
            if len(plan['tasks']) >= 2 and providers <= {'codex', 'gemini'} and providers != {'codex', 'gemini'}:
                print("A equipe precisa manter pelo menos um agente Codex e um agente Gemini.")
                continue
            return plan
        if not selected.isdigit() or not 1 <= int(selected) <= len(plan['tasks']):
            print(f"Escolha um número de 1 a {len(plan['tasks'])}.")
            continue
        task = plan['tasks'][int(selected) - 1]
        candidates = [dict(model, runner=runner)
                      for runner in ('gemini', 'codex', 'opencode', 'claude')
                      for model in catalogs.get(runner, [])]
        if not candidates:
            print("Não foi possível obter a lista de modelos dos clientes.")
            continue
        from .ui import bold, dim
        curr_prov = provider_name_for(task['runner'])
        curr_mod = task.get('model') or 'padrão'
        print(f"\n🤖 {bold(task['role'])} • atual: {curr_prov} / {curr_mod}")
        for index, model in enumerate(candidates, 1):
            flash = " ⚡ Flash" if '-flash-' in model['id'] else ""
            free = " 🎁 Grátis" if model.get('is_free') else ""
            description = " ".join(clean(model.get('description', '')).split())
            detail = f" — {description[:90]}" if description else ""
            key_s = bold(f"{index:>2}.")
            p_name = provider_name_for(model['runner'])
            print(f"  {key_s} {p_name} • {bold(model['id'])}{flash}{free}{dim(detail)}")
        choice = ask("Novo modelo (número; Enter mantém)", "")
        if not choice:
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
            print("Modelo inválido; nenhuma alteração foi feita.")
            continue
        chosen = candidates[int(choice) - 1]
        task['runner'], task['model'] = chosen['runner'], chosen['id']
        print(f"✅ {task['role']} usará {provider_name_for(task['runner'])} • {task['model']}.")


def provider_name_for(runner):
    if runner == 'gemini':
        return 'Google Antigravity'
    if runner == 'codex':
        return 'Codex'
    if runner == 'opencode':
        return 'OpenCode (Modelos Livres/Abertos)'
    if runner == 'claude':
        return 'Anthropic Claude'
    return runner


def prompt_model_switch(exhausted_provider, task=None):
    """
    Pergunta interativamente quando a cota atinge o limite:
    1 • Deixar pausado e esperar
    2 • Trocar de modelo após verificar limites disponíveis e continuar
    Retorna (target_provider, target_model) se optou por trocar, ou None se preferir pausar.
    """
    if not sys.stdin.isatty():
        return None
    from .ui import box_header, menu_card, bold, dim
    role = f" '{task['role']}'" if task and task.get('role') else ""
    ex_name = provider_name_for(exhausted_provider) if exhausted_provider in ('codex', 'gemini', 'opencode', 'claude') else (exhausted_provider or 'atual')
    print(box_header(f"⚠️  LIMITE DE COTA: {ex_name.upper()}", f"A execução do agente{role} atingiu a cota temporária."))
    items = [
        ("1", "⏸  Aguardar cota", "(deixar execução pausada até liberação)"),
        ("2", "🔄 Continuar agora", "(escolher outro modelo com cota liberada)"),
    ]
    print("\n" + menu_card(items, title="Opções de Recuperação"))
    decision = ask("Sua escolha", "2")
    if decision != "2":
        print("Execução mantida como pausada. Você pode retomá-la mais tarde.")
        return None

    catalogs = model_catalogs()
    candidates = []
    # Prioritize other providers with available quota
    other_runners = [r for r in ('gemini', 'opencode', 'codex', 'claude') if r != exhausted_provider]
    for runner in other_runners:
        for m in catalogs.get(runner, []):
            candidates.append({**m, "runner": runner})

    if not candidates:
        fallback_runner = "gemini" if exhausted_provider != "gemini" else "codex"
        candidates = [{"id": "gemini-3.8-flash-high", "runner": fallback_runner, "description": "Modelo padrão"}]

    print(f"\n🔍 {bold('Todos os modelos com limite disponível:')}")
    for idx, m in enumerate(candidates, 1):
        flash = " ⚡ Flash" if "-flash-" in m['id'] else ""
        pro = " 🧠 Pro" if "-pro" in m['id'] else ""
        free = " 🎁 Grátis / Rotação Livre" if m.get("is_free") else ""
        desc = " ".join(clean(m.get('description', '')).split())
        detail = f" — {desc[:70]}" if desc else ""
        pname = provider_name_for(m['runner'])
        num = bold(f"{idx:>2}.")
        print(f"  {num} {pname} • {bold(m['id'])}{flash}{pro}{free}{dim(detail)}")

    choice = ask(f"Escolha o modelo desejado (1 a {len(candidates)}; Enter para padrão)", "1")
    chosen_item = None
    if choice.isdigit() and 1 <= int(choice) <= len(candidates):
        chosen_item = candidates[int(choice) - 1]
    else:
        chosen_item = candidates[0]
    print(f"✅ Selecionado: {provider_name_for(chosen_item['runner'])} • {chosen_item['id']}")
    return chosen_item['runner'], chosen_item['id']


def detect_rounds(objective):
    """Detect if an objective requests iterative rounds/cycles/debates."""
    if not objective or not isinstance(objective, str):
        return None
    match = re.search(r'(\d+)\s*(?:rodadas?|ciclos?|itera[çc][õo]es|rounds?|etapas? de melhoria)', objective, re.IGNORECASE)
    if match:
        num = int(match.group(1))
        return num if 2 <= num <= 10 else (None if num <= 1 else 10)
    if re.search(r'\b(rodadas?|ciclos?|itera[çc][õo]es|debate|bate-volta|revis[ãa]o cruzada)\b', objective, re.IGNORECASE):
        return 3
    return None


def team_contract(plan, count, catalogs, total_rounds=1):
    validate(plan)
    expected = count * total_rounds
    if len(plan['tasks']) != expected:
        msg = f"O coordenador deve criar exatamente {expected} tarefas ({count} agentes x {total_rounds} rodadas)" if total_rounds > 1 else f"O coordenador deve criar exatamente {count} agentes/tarefas"
        raise ValueError(msg)
    providers = {t['runner'] for t in plan['tasks']}
    valid_runners = {'codex', 'gemini', 'opencode'}
    if not providers <= valid_runners:
        raise ValueError('Plano real não pode usar simulação')
    if count >= 2 and len(providers) < 2:
        raise ValueError('A equipe deve incluir múltiplos provedores (ex: Codex e Gemini)')
    for task in plan['tasks']:
        if not task.get('summary'):
            raise ValueError(f"{task['id']}: inclua summary curto para a interface")
        available = {m['id'] for m in catalogs.get(task['runner'], [])}
        if available and task.get('model') not in available:
            raise ValueError(f"{task['id']}: escolha um modelo disponível para {task['runner']}")
        if available and not task.get('model'):
            raise ValueError(f"{task['id']}: informe explicitamente o modelo")
        if task.get('model') and available and task['model'] not in available:
            raise ValueError(f"{task['id']}: modelo fora do catálogo local")
    return plan


def demo_team(objective, count, total_rounds=1):
    if total_rounds <= 1:
        tasks = []
        for i in range(count):
            tasks.append({'id': f'agente_{i+1}', 'role': 'Integrador' if i == count-1 else f'Especialista {i+1}',
                'runner': 'demo', 'stage': 'Integração' if i == count-1 else 'Preparação',
                'summary': 'Consolidar as entregas da equipe.' if i == count-1 else f'Produzir a entrega {i+1}.',
                'prompt': f'Exemplo simulado para: {objective}', 'outputs': [f'entrega_{i+1}.md'],
                'depends_on': [t['id'] for t in tasks] if i == count-1 else []})
        return validate({'version': 2, 'objective': objective + ' [SIMULAÇÃO]', 'tasks': tasks})

    tasks = []
    for r in range(1, total_rounds + 1):
        prev_round_last_task = f'agente_r{r-1}_{count}' if r > 1 else None
        for i in range(1, count + 1):
            task_id = f'agente_r{r}_{i}'
            is_round_last = (i == count)
            is_final = (r == total_rounds and is_round_last)
            
            if i == 1:
                deps = [prev_round_last_task] if prev_round_last_task else []
            else:
                prev_task = f'agente_r{r}_{i-1}'
                deps = [prev_task]
                
            role = 'Integrador Final' if is_final else ('Consolidador da Rodada' if is_round_last else f'Especialista {i}')
            stage = f'Rodada {r}'
            output_name = 'entrega_final.md' if is_final else (f'resultado_rodada_{r}.md' if is_round_last else f'entrega_r{r}_{i}.md')
            
            tasks.append({
                'id': task_id,
                'role': role,
                'stage': stage,
                'runner': 'demo',
                'summary': f'Produzir {output_name} na {stage}.',
                'prompt': f'Exemplo simulado para {stage}: {objective}',
                'outputs': [output_name],
                'depends_on': deps
            })
    return validate({'version': 2, 'objective': objective + ' [SIMULAÇÃO]', 'tasks': tasks})


async def progress(run, workers=1, interactive_quota=True):
    resolver = prompt_model_switch if (interactive_quota and sys.stdin.isatty()) else None
    future = asyncio.create_task(run_engine(run, {'total': workers, 'codex': 1,
                                                 'gemini': 1, 'demo': workers},
                                            quota_resolver=resolver))
    previous = None
    try:
        while not future.done():
            state = read_json(run/'state.json')
            message = ' | '.join(f"{name}: {s.get('activity', s['status'])}" for name,s in state['tasks'].items())
            if message != previous:
                print(clean(message), flush=True)
                previous = message
            await asyncio.sleep(.5)
        return await future
    finally:
        if not future.done():
            future.cancel()
            await asyncio.gather(future, return_exceptions=True)


def guided_plan(objective, count, root, input_paths=None, total_rounds=1):
    from .cli import demo_plan
    catalogs = model_catalogs()
    coordinator_model = planner_model(catalogs['codex'])
    example = demo_plan()
    for t in example['tasks']:
        t['runner'] = 'gemini' if t['id'] == 'documentacao' else 'codex'
    total_tasks = count * total_rounds
    round_guidance = (
        f'PIPELINE CÍCLICA EM {total_rounds} RODADAS:\n'
        f'O plano deve conter exatamente {total_tasks} tarefas divididas em {total_rounds} rodadas de {count} agentes cada.\n'
        f'Para cada rodada r de 1 a {total_rounds} (stage: "Rodada 1", "Rodada 2", etc.):\n'
        f'- Os {count} agentes formam a pipeline da rodada (ex: {count} papéis especializados complementares).\n'
        f'- O último agente de cada rodada gera o arquivo consolidado daquela rodada (ex: resultado_rodada_1.md).\n'
        f'- Na rodada seguinte, o primeiro agente OBRIGATORIAMENTE depende do último agente da rodada anterior e lê sua entrega em inputs/<agente_anterior>/<arquivo>, '
        f'colocando o resultado da rodada anterior de volta no início da pipeline!\n'
        f'- A última tarefa da rodada {total_rounds} integra a entrega final definitiva.\n'
    ) if total_rounds > 1 else (
        'Se o objetivo solicitar rodadas, debate, conversa, interação ou revisão entre agentes: '
        'estruture tarefas sequenciais em rodadas explícitas com dependências alternadas entre diferentes agentes '
        '(ex: rodada 1 proposta com um agente, rodada 1 crítica/revisão com outro agente, rodada 2 refinamento, até a integração final).\n'
    )
    instruction = (
        f'Crie plan.json para o objetivo: {objective}. Exatamente {total_tasks} tarefas, cada uma é um agente/etapa. '
        f'Arquivos fornecidos estarão disponíveis para todos em inputs/usuario/: '
        f'{json.dumps([Path(p).name for p in (input_paths or [])], ensure_ascii=False)}. '
        'Não execute o trabalho agora. Para dois ou mais agentes, use obrigatoriamente codex E gemini. '
        'Escolha o provedor conforme a adequação à atividade, buscando complementaridade e revisão cruzada. '
        f'{round_guidance}'
        'Distribua equipes com três ou mais agentes aproximadamente pela metade entre codex e gemini. '
        'Escolha o modelo de CADA tarefa usando apenas os catálogos abaixo, ponderando complexidade e consumo. '
        'Prefira modelos Gemini Flash para pesquisa, leitura, extração, síntese, documentação e revisões rápidas. '
        'Reserve Gemini Pro e modelos Codex mais pesados para raciocínio complexo, auditoria crítica ou integração difícil. '
        'Para tarefas do runner gemini, escolha somente modelos cujo ID começa com gemini-. '
        'Não invente nomes. Se o catálogo do provedor estiver vazio, omita model. '
        'O catálogo local não comprova cota ou acesso atual. '
        'Cada tarefa deve ter summary: uma frase simples, com no máximo 100 caracteres, que diga somente o que o agente fará. '
        'O prompt contém os detalhes técnicos, sem repetir regras gerais e sem explicar a escolha do modelo. '
        'Cada agente recebe apenas inputs/<dependência>/<arquivo>; declare todos os arquivos finais em outputs. '
        'Inclua critérios de aceitação com checks compatíveis. Os ÚNICOS checks permitidos são: '
        '{"kind": "contains", "path": "arquivo", "text": "trecho"} para verificar texto; '
        '{"kind": "json", "path": "arquivo.json"} para validar sintaxe JSON; '
        '{"kind": "python", "path": "arquivo.py"} para validar código Python; '
        '{"kind": "json_equals", "path": "arquivo.json", "field": "campo", "value": "valor"}. '
        'NÃO invente outros checks (como file_exists, content_contains, json_schema, etc); se não precisar de check, use "checks": []. '
        'Timeout=600, retries=1. Não use ferramentas que exijam acesso fora da pasta. O único campo adicional ao exemplo é summary. '
        f'Catálogos por runner: {json.dumps(catalogs, ensure_ascii=False)}. '
        f'Formato exemplo (adapte a quantidade): {json.dumps(example, ensure_ascii=False)}')
    for attempt in range(2):
        plan = validate({'version':2, 'objective':objective, 'tasks':[{
            'id':'planejador','role':'Coordenador','stage':'Planejamento','runner':'codex',
            'model':coordinator_model,
            'summary':'Montar a equipe e organizar as etapas.',
            'prompt':instruction, 'outputs':['plan.json'], 'checks':[{'kind':'plan','path':'plan.json'}],
            'timeout':600,'retries':1}]})
        run = create_run(plan, root)
        print(f"🧭 Coordenador: Codex • modelo {coordinator_model or 'padrão do cliente'}")
        print('O coordenador está montando a equipe. Você não precisa copiar caminhos.')
        res_status = asyncio.run(progress(run))
        if res_status != 'succeeded':
            state = read_json(run / 'state.json')
            task_err = state.get('tasks', {}).get('planejador', {}).get('error', '')
            is_quota = state.get('status') == 'paused' or any(x in str(task_err).lower() for x in ('usage limit', 'quota', 'rate limit', '429'))
            if is_quota:
                print(f"\n⚠️ Limite de uso do Codex (ChatGPT) atingido durante o planejamento!")
                print("💡 O Google Antigravity (Gemini) está configurado e pode assumir o planejamento.")
                trocar = ask("Deseja alternar o planejamento para Google Antigravity (Gemini)? (s/n)", "s")
                if trocar.lower() in ('s', 'sim'):
                    gemini_candidates = catalogs.get('gemini', [])
                    gemini_model = next((m['id'] for m in gemini_candidates if 'pro' in m['id']), None)
                    if not gemini_model and gemini_candidates:
                        gemini_model = gemini_candidates[0]['id']
                    print(f"🧭 Coordenador: Google Antigravity • modelo {gemini_model or 'padrão do cliente'}")
                    plan_gem = validate({'version': 2, 'objective': objective, 'tasks': [{
                        'id': 'planejador', 'role': 'Coordenador', 'stage': 'Planejamento', 'runner': 'gemini',
                        'model': gemini_model,
                        'summary': 'Montar a equipe e organizar as etapas.',
                        'prompt': instruction, 'outputs': ['plan.json'], 'checks': [{'kind': 'plan', 'path': 'plan.json'}],
                        'timeout': 600, 'retries': 1}]})
                    run_gem = create_run(plan_gem, root)
                    if asyncio.run(progress(run_gem)) == 'succeeded':
                        try:
                            res = team_contract(load(run_gem / 'artifacts/planejador/plan.json'), count, catalogs, total_rounds)
                            p_task = load(run_gem / 'plan.json').get('tasks', [{}])[0]
                            res['_coordinator'] = {'runner': p_task.get('runner', 'gemini'), 'model': p_task.get('model') or gemini_model or 'gemini-3.1-pro-high'}
                            return res
                        except ValueError as exc:
                            print('Ajustando o plano:', exc)
                            instruction += f' Corrija a validação anterior: {exc}.'
                            continue
            print(f'Não foi possível planejar. Detalhes preservados em {run}. Use Configurar no menu para conferir login e dependências.')
            return None
        try:
            res = team_contract(load(run/'artifacts/planejador/plan.json'), count, catalogs, total_rounds)
            p_task = load(run / 'plan.json').get('tasks', [{}])[0]
            res['_coordinator'] = {'runner': p_task.get('runner', 'codex'), 'model': p_task.get('model') or coordinator_model or 'gpt-6-astra'}
            return res
        except ValueError as exc:
            print('Ajustando o plano:', exc)
            instruction += f' Corrija a validação anterior: {exc}.'
    print('O plano ainda não atende ao contrato. Nenhum executor foi iniciado.')
    return None


def configure():
    from .cli import doctor
    from .runners import opencode_bin
    from .ui import menu_card
    doctor()
    items = [
        ("1", "📦 Instalar clientes ausentes", "(setup automatizado)"),
        ("2", "🔑 Abrir login ChatGPT/Codex", "(codex login)"),
        ("3", "🌐 Abrir Google Antigravity", "(login e modelo no agy)"),
        ("4", "🔓 Gerenciar contas / OpenCode", "(modelos livres / rotação)"),
        ("5", "🟣 Abrir login Anthropic Claude", "(claude auth login / Pro)"),
        ("6", "🖥️  Preparar tmux", "(multiplexador de terminais)"),
        ("0", "↩  Voltar", "(retornar ao menu anterior)")
    ]
    print("\n" + menu_card(items, title="Ações de Configuração"))
    choice = ask('Escolha', '0')
    if choice in ('2', '3', '4', '5'):
        if choice == '2':
            command = ['codex', 'login']
        elif choice == '3':
            command = ['agy']
        elif choice == '4':
            command = [opencode_bin(), 'providers']
        else:
            command = ['claude', 'auth', 'login']
        bin_target = command[0]
        if shutil.which(bin_target) or Path(bin_target).exists():
            subprocess.run(command, check=False)
        else:
            print('Cliente ausente. Use a opção de instalação primeiro.')
    elif choice == '1':
        from .setup import install_clients, install_system
        install_clients()
        if ask('Preparar também o tmux? (s/n)', 's').lower() in ('s', 'sim'):
            install_system()
    elif choice == '6':
        from .setup import install_system
        install_system()


def restart_run(run):
    """Revert changes and reset a run to start from scratch."""
    from .cli import stop
    run = Path(run).resolve()
    stop(run)
    plan = read_json(run / "plan.json")

    new_state = {
        "status": "ready",
        "created_at": time.time(),
        "objective": plan["objective"],
        "plan_hash": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest(),
        "user_inputs": read_json(run / "state.json").get("user_inputs", []),
        "tasks": {
            t["id"]: {
                "status": "pending",
                "attempts": 0,
                "activity": "Aguardando início",
                "error": None,
                "hashes": {}
            }
            for t in plan["tasks"]
        }
    }
    write_json(run / "state.json", new_state)

    artifacts_dir = run / "artifacts"
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    work_dir = run / "work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    coord_log = run / "coordinator.log"
    if coord_log.exists():
        coord_log.unlink()
    logs_dir = run / "logs"
    if logs_dir.exists():
        for f in logs_dir.glob("*"):
            if f.is_file():
                f.unlink()
    else:
        logs_dir.mkdir(parents=True, exist_ok=True)

    name = "agentes-" + re.sub(r"[^a-zA-Z0-9_-]", "-", run.name)
    subprocess.run(["tmux", "kill-session", "-t", name], check=False, capture_output=True)

    return run


def previous_runs(root):
    from .monitor import dashboard, watch
    from .engine import fork_or_migrate_run
    from .ui import box_header, menu_card, table, badge, bold, dim
    paths = sorted(Path(root).glob('*/state.json'), reverse=True)[:20]
    if not paths:
        print(box_header("📋 HISTÓRICO DE ATIVIDADES", "Nenhuma atividade salva ainda nesta pasta."))
        return

    print(box_header("📋 HISTÓRICO DE ATIVIDADES", "Selecione uma atividade para acompanhar, retomar ou gerenciar"))
    print()
    headers = ["#", "STATUS", "OBJETIVO DA ATIVIDADE"]
    rows = []
    for i, path in enumerate(paths, 1):
        state = read_json(path)
        raw_status = state.get('status', 'unknown')
        status_label = raw_status.upper()
        badge_kind = "info"
        if raw_status == "succeeded":
            badge_kind = "success"
            status_label = "CONCLUÍDO"
        elif raw_status in ("failed", "interrupted"):
            badge_kind = "error"
            status_label = "FALHOU" if raw_status == "failed" else "INTERROMPIDO"
        elif raw_status == "paused":
            badge_kind = "warning"
            exhausted = state.get('exhausted_provider')
            for s in state.get('tasks', {}).values():
                if not exhausted and s.get('status') == 'paused' and s.get('error'):
                    err = str(s['error']).lower()
                    if any(w in err for w in ('usage limit', 'quota', 'rate limit', '429')):
                        exhausted = 'codex' if ('codex' in err or 'chatgpt' in err) else 'gemini'
                        break
            if exhausted:
                status_label = f"PAUSADO ({provider_name_for(exhausted)})"
            else:
                status_label = "PAUSADO"
        elif raw_status == "running":
            badge_kind = "running"
            status_label = "EXECUTANDO"

        st_badge = badge(status_label, badge_kind)
        obj_text = clean(state.get('objective', 'Sem descrição'))
        if len(obj_text) > 55:
            obj_text = obj_text[:52] + "..."
        rows.append([bold(str(i)), st_badge, obj_text])

    print(table(headers, rows))
    selected = ask('\nNúmero da atividade (Enter volta)', '')
    if not selected:
        return
    if not selected.isdigit() or not 1 <= int(selected) <= len(paths):
        print('Seleção inválida.')
        return
    run = paths[int(selected)-1].parent
    state = read_json(run / 'state.json')

    actions = [
        ("1", "👁  Acompanhar painel", "(dashboard ao vivo)"),
        ("2", "▶  Retomar execução", "(continua tarefas pendentes)"),
        ("3", "🔄 Reiniciar do zero", "(limpa tentativas e refaz)"),
        ("4", "🔀 Trocar provedor / cota", "(migrar para modelo disponível)"),
        ("5", "⏹  Interromper execução", "(parar agentes suavemente)"),
        ("6", "🧭 Conversar com Coordenador", "(avaliar entregas / nova rodada)"),
        ("0", "↩  Voltar", "")
    ]
    obj_short = clean(state.get('objective', ''))[:40]
    print("\n" + menu_card(actions, title=f"Ações para: {obj_short}"))
    action = ask('Escolha uma ação', '1')
    if action == '1':
        if shutil.which('tmux'):
            dashboard(run)
        else:
            watch(run)
    elif action == '2':
        exhausted = state.get('exhausted_provider')
        if not exhausted:
            for s in state.get('tasks', {}).values():
                if s.get('status') == 'paused' and s.get('error'):
                    err = str(s['error']).lower()
                    if any(w in err for w in ('usage limit', 'quota', 'rate limit', '429')):
                        exhausted = 'codex' if ('codex' in err or 'chatgpt' in err) else 'gemini'
                        break
        if exhausted:
            sug = 'Google Antigravity (Gemini)' if exhausted == 'codex' else 'Codex'
            print(f"\n⚠️ Atenção: Esta execução pausou por limite de cota em {provider_name_for(exhausted)}.")
            print(f"💡 Sugestão: Use a opção 4 para migrar tarefas para {sug}.")
            if ask("Tentar retomar mesmo assim sem trocar provedor? (s/n)", "n").lower() not in ('s', 'sim'):
                return
        print('Retomando apenas tarefas não concluídas.')
        print('Resultado:', asyncio.run(progress(run)))
    elif action == '3':
        confirm = ask("⚠️ Deseja apagar as tentativas/entregas e reiniciar esta atividade do zero? (s/n)", "n")
        if confirm.lower() in ('s', 'sim'):
            restart_run(run)
            print(f"✅ Atividade reiniciada do zero. Todas as tarefas voltaram ao início.")
            if ask("Iniciar a execução agora? (s/n)", "s").lower() in ('s', 'sim'):
                team_sel = read_json(run / 'team-selection.json') if (run / 'team-selection.json').exists() else {}
                plan = read_json(run / 'plan.json')
                count = team_sel.get('requested_agents', len(plan['tasks']))
                if shutil.which('tmux'):
                    from .cli import launch
                    from argparse import Namespace
                    launch(run, Namespace(background=True, dashboard=True, workers=count,
                                         codex_workers=1, gemini_workers=1, opencode_workers=2, demo_delay=2))
                else:
                    print("Executando...")
                    res = asyncio.run(progress(run, count))
                    print("Resultado:", res, "| Entregas:", run / "artifacts")
    elif action == '4':
        exhausted = state.get('exhausted_provider')
        print(f"\n🔄 Migração para Modelos com Limite Disponível")
        catalogs = model_catalogs()
        candidates = []
        for runner in ('gemini', 'opencode', 'codex'):
            if runner != exhausted:
                for m in catalogs.get(runner, []):
                    candidates.append({**m, "runner": runner})
        if not candidates:
            print("Nenhum modelo disponível para migração.")
            return

        print(f"\nModelos disponíveis para continuar a atividade:")
        for idx, m in enumerate(candidates, 1):
            flash = " ⚡ Flash" if "-flash-" in m['id'] else ""
            free = " 🎁 Grátis" if m.get("is_free") else ""
            pname = provider_name_for(m['runner'])
            desc = " ".join(clean(m.get('description', '')).split())[:60]
            detail = f" — {desc}" if desc else ""
            print(f"{idx}. {pname} • {m['id']}{flash}{free}{detail}")

        choice = ask(f"Escolha o novo modelo (1 a {len(candidates)}; Enter para o 1º): ", "1")
        chosen = candidates[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(candidates) else candidates[0]
        print(f"✅ Migrando para: {provider_name_for(chosen['runner'])} • {chosen['id']}")
        try:
            new_run = fork_or_migrate_run(run, chosen['runner'], chosen['id'])
            print(f"\n🚀 Nova execução criada: {new_run}")
            if ask("Deseja iniciar a execução agora? (s/n)", "s").lower() in ('s', 'sim'):
                if shutil.which('tmux'):
                    from .cli import launch
                    from argparse import Namespace
                    launch(new_run, Namespace(background=True, dashboard=True, workers=3,
                                             codex_workers=1, gemini_workers=1, opencode_workers=2, demo_delay=2))
                else:
                    print("Executando...")
                    res = asyncio.run(progress(new_run))
                    print("Resultado:", res, "| Entregas:", new_run / "artifacts")
        except Exception as exc:
            print("Erro ao migrar execução:", exc)
    elif action == '5':
        from .cli import stop
        stop(run)
    elif action == '6':
        from .review import review_with_coordinator
        review_with_coordinator(run, root)


def start(root=Path('runs')):
    from .cli import launch
    from argparse import Namespace
    from .ui import box_header, menu_card
    print(box_header("🧭 AGENTES SEM API — Assistente de Atividades",
                     "Coordenação autônoma com múltiplos agentes, modelos e rodadas"))
    while True:
        menu_items = [
            ("1", "🚀 Nova atividade real", "(utiliza suas contas conectadas)"),
            ("2", "🧪 Experimentar / Demonstração", "(sem consumir assinaturas)"),
            ("3", "⚙️  Configurar / Conectar contas", "(diagnóstico e credenciais)"),
            ("4", "📋 Acompanhar / Retomar", "(atividades anteriores)"),
            ("0", "🚪 Sair", "")
        ]
        print("\n" + menu_card(menu_items, title="Menu Principal"))
        try:
            choice = ask('Escolha', '1')
            if choice == '0':
                return
            if choice == '4':
                previous_runs(root)
                continue
            if choice == '3':
                configure()
                continue
            if choice not in ('1','2'):
                continue
            if choice == '1':
                missing = [p for p in ('codex','agy') if not shutil.which(p)]
                if missing:
                    print('Para montar uma equipe mista faltam: ' + ', '.join(missing) + '. Use Configurar no menu.')
                    continue
                if any(os.environ.get(key) for key in API_ENV):
                    print('Há variáveis de API no ambiente. Use um ambiente de assinatura sem elas; veja Configurar.')
                    continue
            objective = ask('🎯 O que você quer que a equipe faça?')
            if not objective:
                print('Descreva uma atividade antes de continuar.')
                continue
            detected = detect_rounds(objective)
            total_rounds = 1
            if detected and detected > 1:
                msg = f"🔄 Identificado pedido de {detected} rodadas de melhoria. Executar em pipeline cíclica (onde a saída de cada ciclo volta ao início para a próxima rodada)? (s/n)"
                if ask(msg, "s").lower() in ('s', 'sim'):
                    total_rounds = detected
                    print(f"✅ Pipeline cíclica ativada: {total_rounds} rodadas.")
            count = agent_count()
            input_paths = choose_inputs() if choice == '1' else []
            if count == 1 and total_rounds == 1:
                print('Com um executor não há equipe mista de executores; para usar ambos escolha 2 ou mais.')
            plan = demo_team(objective, count, total_rounds) if choice == '2' else guided_plan(objective, count, root, input_paths, total_rounds)
            if plan is None:
                continue
            print('\n📋 Equipe proposta')
            for index, t in enumerate(plan['tasks'], 1):
                provider = provider_name_for(t['runner']) if choice == '1' else t['runner'].capitalize()
                dependencies = ', '.join(t['depends_on']) or 'nenhuma'
                print(clean(f"{index}. {t['role']}\n   🎯 {t.get('summary') or t['prompt'][:100]}\n"
                            f"   🤖 {provider} • {t.get('model') or 'modelo padrão'} • depende de: {dependencies}"))
            if choice == '1':
                print("\nVocê pode aceitar as escolhas ou trocar qualquer modelo antes de iniciar.")
                edit_models(plan, model_catalogs())
            if ask('Iniciar esta equipe? (s/n)', 's').lower() not in ('s','sim'):
                continue
            coord_info = plan.get('_coordinator', {'runner': 'codex', 'model': 'gpt-6-astra'} if choice == '1' else {'runner': 'demo', 'model': 'demo-coordenador'})
            plan.pop('_coordinator', None)
            run = create_run(plan,root,input_paths)
            write_json(run/'team-selection.json', {
                'requested_agents': count, 'source': 'guided',
                'coordinator': coord_info,
                'total_rounds': total_rounds,
                'models': [{k:t.get(k) for k in ('id','runner','model')} for t in plan['tasks']]
            })
            if shutil.which('tmux'):
                launch(run, Namespace(background=True,dashboard=True,workers=count,
                                     codex_workers=1,gemini_workers=1,demo_delay=2))
                print('Ao sair dos painéis, o trabalho continua. Entregas:', run/'artifacts')
            else:
                print('Sem tmux: acompanhando aqui no terminal.')
                result = asyncio.run(progress(run,count))
                print('Resultado:',result,'| Entregas:',run/'artifacts')
        except (EOFError, KeyboardInterrupt):
            print('\nAssistente encerrado.')
            return
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print('Não foi possível concluir:',clean(exc))
