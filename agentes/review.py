"""Post-execution coordinator review and continuation run dispatching."""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from .engine import create_run
from .manifest import validate
from .storage import read_json, write_json


def collect_deliverables(run):
    """Scan all output artifact files from completed tasks."""
    run = Path(run)
    artifacts_dir = run / "artifacts"
    deliverables = []
    if artifacts_dir.exists():
        for task_dir in sorted(artifacts_dir.glob("*")):
            if task_dir.is_dir():
                files = [f for f in sorted(task_dir.iterdir()) if f.is_file() and not f.is_symlink()]
                if files:
                    deliverables.append((task_dir.name, files))
    return deliverables


def build_review_prompt(objective, deliverables, user_feedback, catalogs):
    """Build the prompt instructing the coordinator to assess user feedback."""
    snippets = []
    for task_id, files in deliverables:
        for f in files:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")[:2000]
                snippets.append(f"--- Início do arquivo '{f.name}' (gerado por '{task_id}') ---\n{content}\n--- Fim da amostra ---")
            except Exception:
                snippets.append(f"Arquivo '{f.name}' gerado por '{task_id}' (conteúdo binário/não legível).")
    
    deliverables_text = "\n\n".join(snippets) if snippets else "Nenhum artefato disponível."

    return (
        f"Você é o Coordenador da equipe de agentes de IA.\n"
        f"O objetivo original da atividade foi: '{objective}'.\n\n"
        f"As seguintes entregas foram produzidas pela equipe na rodada anterior:\n"
        f"{deliverables_text}\n\n"
        f"O usuário analisou os resultados e enviou o seguinte feedback/solicitação de revisão:\n"
        f"\"{user_feedback}\"\n\n"
        f"Sua missão é:\n"
        f"1. Analisar criticamente o feedback do usuário em relação às entregas realizadas e ao contexto da solicitação.\n"
        f"2. Decidir entre DUAS alternativas fundamentais:\n"
        f"   A) AÇÃO DIRETA NO SISTEMA (necessita_nova_rodada: false):\n"
        f"      - Use se o usuário pediu para MOVER, COPIAR ou EXPORTAR um documento/arquivo existente para uma pasta (ex.: ~/Downloads, ~/Desktop ou caminho específico), OU se fez apenas uma pergunta/esclarecimento que você mesmo responde na sua avaliação.\n"
        f"      - NÃO crie tarefas para a equipe e NÃO reinicie a fila! O sistema executará a ação de arquivo diretamente e encerrará a interação de forma limpa.\n"
        f"   B) NOVA RODADA DE TRABALHO TÉCNICO (necessita_nova_rodada: true):\n"
        f"      - Use APENAS se o usuário solicitou novas análises, melhorias de código, novas seções, testes ou novas rodadas de debate entre agentes especialistas.\n"
        f"3. Explicar claramente ao usuário sua avaliação técnica no campo 'avaliacao'.\n"
        f"4. Se for ação direta de arquivo, preencher 'acao_direta' com 'tipo', 'origem' e 'destino'.\n"
        f"5. Se necessita_nova_rodada for true, estruturar 'novas_tarefas' para os agentes.\n\n"
        f"DIRETRIZES PARA AS NOVAS TAREFAS (somente se necessita_nova_rodada for true):\n"
        f"- Todos os arquivos entregues na rodada anterior estarão disponíveis para os novos agentes na pasta inputs/usuario/<nome_do_arquivo>.\n"
        f"- Escolha runners e modelos usando apenas os catálogos disponíveis: {json.dumps(catalogs, ensure_ascii=False)}.\n"
        f"- Se o feedback solicitar rodadas adicionais, debate ou revisão cruzada entre agentes, crie tarefas sequenciais encadeadas onde um agente revisa ou complementa o outro.\n"
        f"- Cada tarefa DEVE conter os campos:\n"
        f"  * id: identificador único (a-z, 0-9, _)\n"
        f"  * role: papel descritivo do agente\n"
        f"  * stage: nome da etapa\n"
        f"  * runner: 'codex', 'gemini', 'opencode' ou 'claude'\n"
        f"  * model: ID do modelo do catálogo\n"
        f"  * summary: resumo curto de até 100 caracteres do que o agente fará\n"
        f"  * prompt: instrução técnica detalhada orientando a ler inputs/usuario/<arquivo>\n"
        f"  * outputs: lista de arquivos que o agente deve produzir\n"
        f"  * depends_on: lista de tarefas das quais depende nesta rodada (ou lista vazia)\n"
        f"  * checks: lista de verificações (ou [])\n\n"
        f"Gere OBRIGATORIAMENTE um arquivo parecer.json com a seguinte estrutura exata:\n"
        f'{{\n'
        f'  "avaliacao": "Sua explicação detalhada para o usuário sobre o feedback e o que foi decidido",\n'
        f'  "necessita_nova_rodada": false,\n'
        f'  "acao_direta": {{\n'
        f'    "tipo": "copiar_arquivo",\n'
        f'    "origem": "nome_do_arquivo.md",\n'
        f'    "destino": "~/Downloads"\n'
        f'  }},\n'
        f'  "novas_tarefas": []\n'
        f'}}\n'
    )


def detect_and_execute_direct_action(user_feedback, parecer, deliverables, run):
    """
    Detect if the user request or coordinator parecer corresponds to a direct file action
    (e.g., copying or moving deliverables to ~/Downloads, ~/Desktop, or another local path).
    If detected, execute the copy/move directly and return a dict with details:
    {"executed": True, "action": "copiar_arquivo", "source": Path, "dest": Path, "message": str}
    Otherwise return {"executed": False}.
    """
    all_files = [f for _, files in deliverables for f in files if f.is_file() and not f.is_symlink()]
    if not all_files:
        return {"executed": False}

    acao = parecer.get("acao_direta") if isinstance(parecer.get("acao_direta"), dict) else {}
    raw_tasks = parecer.get("novas_tarefas", []) if isinstance(parecer.get("novas_tarefas"), list) else []

    tipo = str(acao.get("tipo", "")).strip().lower()
    origem = str(acao.get("origem", "")).strip()
    destino = str(acao.get("destino", "")).strip()

    # Check if coordinator created a single dummy task solely for delivery/copy/move
    is_dummy_delivery_task = False
    if len(raw_tasks) == 1 and isinstance(raw_tasks[0], dict):
        t = raw_tasks[0]
        text_task = f"{t.get('id', '')} {t.get('summary', '')} {t.get('prompt', '')}".lower()
        if any(w in text_task for w in ["copiar", "mover", "entrega", "download", "desktop"]):
            is_dummy_delivery_task = True
            if not destino:
                if "download" in text_task:
                    destino = "~/Downloads"
                elif "desktop" in text_task or "área de trabalho" in text_task:
                    destino = "~/Desktop"

    # Check user feedback for move/copy intent
    fb_lower = user_feedback.lower()
    user_wants_transfer = any(w in fb_lower for w in [
        "mover", "mova", "movesse", "copiar", "copie", "download", "downloads",
        "desktop", "área de trabalho", "salvar", "guardar", "exportar", "transfira", "pasta de download"
    ])

    if not tipo and (user_wants_transfer or is_dummy_delivery_task):
        tipo = "copiar_arquivo"

    if not destino:
        if "download" in fb_lower:
            destino = "~/Downloads"
        elif "desktop" in fb_lower or "área de trabalho" in fb_lower:
            destino = "~/Desktop"
        else:
            m = re.search(r'([~/\w.-]+(?:/[~/\w.-]+)+)', user_feedback)
            if m:
                destino = m.group(1)

    if not tipo or tipo not in ("copiar_arquivo", "mover_arquivo", "salvar_arquivo", "exportar_arquivo"):
        if not user_wants_transfer and not is_dummy_delivery_task:
            return {"executed": False}

    if not destino:
        return {"executed": False}

    dest_path = Path(os.path.expanduser(destino)).resolve()

    source_file = None
    if origem:
        orig_name = Path(origem).name.lower()
        for f in all_files:
            if f.name.lower() == orig_name:
                source_file = f
                break
        if not source_file:
            for f in all_files:
                if orig_name in f.name.lower():
                    source_file = f
                    break

    if not source_file:
        combined_text = f"{user_feedback} {json.dumps(raw_tasks)} {parecer.get('avaliacao', '')}".lower()
        for f in all_files:
            if f.name.lower() in combined_text:
                source_file = f
                break

    if not source_file:
        finals = [f for f in all_files if "final" in f.name.lower() or "entrega" in f.name.lower()]
        source_file = finals[0] if finals else all_files[0]

    try:
        if dest_path.is_dir() or destino.endswith("/") or not dest_path.suffix:
            dest_path.mkdir(parents=True, exist_ok=True)
            final_dest = dest_path / source_file.name
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            final_dest = dest_path

        shutil.copy2(source_file, final_dest)
        return {
            "executed": True,
            "action": "copiar_arquivo",
            "source": source_file,
            "dest": final_dest,
            "message": f"Arquivo '{source_file.name}' transferido com sucesso para '{final_dest}'."
        }
    except Exception as e:
        return {
            "executed": False,
            "error": str(e)
        }


def sanitize_review_tasks(raw_tasks, catalogs, fallback_runner="gemini"):
    """Validate and sanitize tasks returned by the coordinator in parecer.json."""
    if not isinstance(raw_tasks, list):
        return []
    valid_tasks = []
    seen_ids = set()
    for t in raw_tasks:
        if not isinstance(t, dict):
            continue
        task_id = str(t.get("id", "")).strip()
        if not task_id or not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", task_id) or task_id in seen_ids or task_id == "coordinator":
            task_id = f"tarefa_{len(valid_tasks) + 1}"
        seen_ids.add(task_id)

        runner = t.get("runner")
        if runner not in ("codex", "gemini", "opencode", "claude", "demo"):
            runner = fallback_runner

        model = t.get("model")
        available_models = [m["id"] for m in catalogs.get(runner, [])]
        if available_models and model not in available_models:
            model = available_models[0]

        outputs = [str(o) for o in t.get("outputs", []) if isinstance(o, str) and o.strip()]
        if not outputs:
            outputs = [f"{task_id}.md"]

        summary = str(t.get("summary") or t.get("prompt", "")[:90]).strip()[:100]
        if not summary:
            summary = f"Executar {task_id}"

        prompt = str(t.get("prompt", "")).strip()
        if not prompt:
            prompt = f"Executar a tarefa {task_id} com base nas orientações do coordenador."

        deps = [str(d) for d in t.get("depends_on", []) if isinstance(d, str) and d in seen_ids and d != task_id]

        valid_tasks.append({
            "id": task_id,
            "role": str(t.get("role") or f"Especialista {len(valid_tasks) + 1}").strip(),
            "stage": str(t.get("stage") or "Refinamento").strip(),
            "runner": runner,
            "model": model,
            "summary": summary,
            "prompt": prompt,
            "outputs": outputs,
            "depends_on": deps,
            "checks": [],
            "timeout": 600,
            "retries": 1
        })
    return valid_tasks


def review_with_coordinator(run, root=None, prompt_fn=input, ask_fn=None):
    """Interactive review session with the coordinator for a completed run."""
    from .wizard import ask as default_ask, model_catalogs, provider_name_for, progress, clean, edit_models
    from .cli import launch

    ask = ask_fn or default_ask
    run = Path(run).resolve()
    if not (run / "plan.json").exists() or not (run / "state.json").exists():
        print("Execução não encontrada ou incompleta.")
        return None

    plan = read_json(run / "plan.json")
    state = read_json(run / "state.json")
    objective = plan.get("objective", "Atividade")

    team_sel = read_json(run / "team-selection.json") if (run / "team-selection.json").exists() else {}
    coord = team_sel.get("coordinator", {})
    coord_runner = coord.get("runner") or ("gemini" if shutil.which("agy") else "codex")
    coord_model = coord.get("model")

    deliverables = collect_deliverables(run)
    all_output_files = [str(f) for _, files in deliverables for f in files]

    from .ui import box_header, dim, bold
    coord_prov = provider_name_for(coord_runner)
    coord_m = coord_model or 'padrão'
    print(box_header("🧭 REVISÃO COM O COORDENADOR", f"Provedor: {coord_prov} • Modelo: {coord_m}"))
    print(f"\n  {bold('Objetivo Original')}: {objective}")
    print(f"\n  📦 {bold('Entregas disponíveis nesta execução')}:")
    if deliverables:
        for task_id, files in deliverables:
            file_names = ", ".join(f.name for f in files)
            print(f"     • {bold(task_id)}: {dim(file_names)}")
    else:
        print(f"     {dim('(Nenhum arquivo de entrega encontrado)')}")
    print(f"\n  {dim('Informe o que deseja revisar, corrigir ou onde salvar/mover entregas.')}")
    print(f"  {dim('(Pressione Enter vazio para cancelar e voltar ao monitor)')}")

    try:
        user_feedback = prompt_fn("\n› Seu feedback para o Coordenador: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not user_feedback:
        print("Revisão cancelada pelo usuário.")
        return None

    catalogs = model_catalogs()
    review_prompt = build_review_prompt(objective, deliverables, user_feedback, catalogs)

    runs_root = Path(root).resolve() if root else run.parent
    review_plan = validate({
        "version": 2,
        "objective": f"Revisão com Coordenador: {objective}",
        "tasks": [{
            "id": "coordenador_revisao",
            "role": "Coordenador",
            "stage": "Revisão",
            "runner": coord_runner,
            "model": coord_model,
            "summary": "Avaliar feedback do usuário e estruturar parecer/nova rodada.",
            "prompt": review_prompt,
            "outputs": ["parecer.json"],
            "checks": [{"kind": "json", "path": "parecer.json"}],
            "timeout": 600,
            "retries": 1
        }]
    })

    print(f"\n🧭 O Coordenador ({provider_name_for(coord_runner)}) está avaliando seu feedback...")
    review_run = create_run(review_plan, runs_root)
    status = asyncio.run(progress(review_run))

    parecer_file = review_run / "artifacts/coordenador_revisao/parecer.json"
    parecer = {}
    if status == "succeeded" and parecer_file.exists():
        try:
            parecer = read_json(parecer_file)
        except Exception:
            pass

    # Handle demo or fallback
    if coord_runner == "demo" or not parecer.get("avaliacao"):
        fb_lower = user_feedback.lower()
        if any(w in fb_lower for w in ["mover", "mova", "movesse", "copiar", "copie", "download", "desktop", "salvar", "exportar"]):
            parecer = {
                "avaliacao": "Identifiquei que você deseja transferir o arquivo de entrega para outra pasta do sistema. Executando ação direta sem reiniciar a fila.",
                "necessita_nova_rodada": False,
                "acao_direta": {
                    "tipo": "copiar_arquivo",
                    "origem": deliverables[0][1][0].name if (deliverables and deliverables[0][1]) else "entrega.md",
                    "destino": "~/Downloads" if "download" in fb_lower else "~/Desktop"
                },
                "novas_tarefas": []
            }
        elif parecer.get("demo") or coord_runner == "demo":
            parecer = {
                "avaliacao": f"Com base no feedback '{user_feedback}', preparei uma nova rodada de aprimoramento para a equipe.",
                "necessita_nova_rodada": True,
                "novas_tarefas": [{
                    "id": "aprimoramento_revisao",
                    "role": "Especialista em Revisão",
                    "stage": "Revisão",
                    "runner": "demo",
                    "summary": "Aprimorar entregas conforme feedback.",
                    "prompt": f"Revisar entregas anteriores com base em: {user_feedback}",
                    "outputs": ["revisao_final.md"],
                    "depends_on": [],
                    "checks": []
                }]
            }
        else:
            print("Não foi possível obter a resposta estruturada do Coordenador.")
            return None

    from .ui import box_header
    print(box_header("🧭 PARECER DO COORDENADOR", "Avaliação técnica das entregas e feedback"))
    print(f"\n{parecer.get('avaliacao', '(Sem comentários adicionais)')}\n")

    # 1. Check for direct actions (moving/copying files) requested by user or coordinator
    direct_res = detect_and_execute_direct_action(user_feedback, parecer, deliverables, run)
    if direct_res.get("executed"):
        print(f"\n✅ {direct_res['message']}")
        print("\n✨ O Coordenador executou a ação solicitada diretamente.")
        print("Fila de tarefas finalizada com sucesso sem necessidade de reiniciar a execução.")
        return None

    needs_new_round = parecer.get("necessita_nova_rodada", False)
    raw_tasks = parecer.get("novas_tarefas", [])
    new_tasks = sanitize_review_tasks(raw_tasks, catalogs, fallback_runner=coord_runner)

    if not needs_new_round or not new_tasks:
        print("\nO Coordenador concluiu que as entregas atuais já atendem plenamente ao objetivo.")
        print("Nenhuma nova tarefa necessária. Fila encerrada.")
        return None

    print(f"\n📋 O Coordenador planejou uma nova rodada com {len(new_tasks)} tarefas:")
    for i, t in enumerate(new_tasks, 1):
        prov = provider_name_for(t["runner"])
        mod = t.get("model") or "padrão"
        deps = ", ".join(t.get("depends_on", [])) or "nenhuma"
        print(clean(f"{i}. {t['role']} ({prov} • {mod})\n   🎯 {t['summary']}\n   🔗 Depende de: {deps}"))

    resp = ask("\nDeseja iniciar esta nova rodada agora? (s/n)", "s")
    if resp.lower() not in ("s", "sim"):
        print("Nova rodada descartada.")
        return None

    edit_models({"tasks": new_tasks}, catalogs, ask_fn=ask)

    continuation_plan = validate({
        "version": 2,
        "objective": f"{objective} [Rodada de Revisão]",
        "tasks": new_tasks
    })

    continuation_run = create_run(continuation_plan, runs_root, input_paths=all_output_files)
    write_json(continuation_run / "team-selection.json", {
        "requested_agents": len(new_tasks),
        "source": "coordinator_review",
        "parent_run": str(run),
        "coordinator": {"runner": coord_runner, "model": coord_model},
        "models": [{k: t.get(k) for k in ("id", "runner", "model")} for t in new_tasks]
    })

    print(f"\n🚀 Nova rodada criada: {continuation_run}")
    if shutil.which("tmux"):
        launch(continuation_run, Namespace(
            background=True, dashboard=True, workers=len(new_tasks),
            codex_workers=1, gemini_workers=1, opencode_workers=2, demo_delay=2
        ))
        if os.environ.get("TMUX"):
            session_name = "agentes-" + re.sub(r"[^a-zA-Z0-9_-]", "-", continuation_run.name)
            subprocess.run(["tmux", "switch-client", "-t", session_name], check=False)
    else:
        print("Acompanhando execução...")
        asyncio.run(progress(continuation_run, len(new_tasks)))

    return continuation_run
