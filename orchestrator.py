import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

BASE_DIR = Path(".").resolve()
RUNS_DIR = BASE_DIR / "runs"
PIPELINE_FILE = BASE_DIR / "pipeline.json"
LOG_FILE = BASE_DIR / "orchestrator.log"

MAX_WORKERS = int(os.environ.get("MAX_WORKERS", 3))

RUNNERS_PERMITIDOS = {"opencode"}
MODELOS_PERMITIDOS = {
    "openai/gpt-5.4-mini",
    "openai/gpt-5.4-mini-fast",
    "openai/gpt-5.4",
    "openai/gpt-5.5",
    "openai/gpt-5.6-luna",
    "openai/gpt-5.6-sol",
    "openai/gpt-5.6-terra",
}

def limpar_ansi(texto: str) -> str:
    """Remove códigos de escape ANSI para gravação em arquivo de texto."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', texto)

def log_evento(msg: str, estilo: str = "white", emite_console: bool = True):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    linha_pura = f"[{timestamp}] {limpar_ansi(msg)}"
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(linha_pura + "\n")
    
    if emite_console:
        console.print(f"[dim]{timestamp}[/dim] {msg}")

def exibir_banner():
    conteudo = (
        f"[bold cyan]Status:[/bold cyan] [bold green]ONLINE[/bold green] - Monitorando [yellow]pipeline.json[/yellow]\n"
        f"[bold cyan]Workers Concorrentes:[/bold cyan] [bold magenta]{MAX_WORKERS}[/bold magenta]\n"
        f"[bold cyan]Runner Ativo:[/bold cyan] OpenCode CLI ([green]ChatGPT Plus OAuth[/green])\n"
        f"[bold cyan]Diretório Base:[/bold cyan] [dim]{BASE_DIR}[/dim]"
    )
    console.print(Panel(conteudo, title="🚀 [bold bright_white]ORQUESTRADOR MULTIAGENTE LOCAL[/bold bright_white]", border_style="bright_blue"))

def exibir_tabela_resumo(run_id: str, status_geral: str, tarefas_resultados: list[dict]):
    cor_status = "green" if status_geral == "SUCESSO" else "red"
    tabela = Table(title=f"📊 Relatório da Run: [bold {cor_status}]{run_id}[/bold {cor_status}] - Status: [bold {cor_status}]{status_geral}[/bold {cor_status}]", border_style="cyan")
    
    tabela.add_column("Ordem", justify="center", style="bold yellow")
    tabela.add_column("ID da Tarefa", style="bold cyan")
    tabela.add_column("Status", justify="center")
    tabela.add_column("Tempo", justify="right", style="magenta")
    tabela.add_column("Artefato Esperado", style="green")
    tabela.add_column("Arquivo Existe", justify="center")
    tabela.add_column("Erro / Detalhes", style="red")

    for r in tarefas_resultados:
        status_badge = "[bold green]✔ SUCESSO[/bold green]" if r["status"] == "SUCESSO" else "[bold red]✘ FALHA[/bold red]"
        existe_badge = "[green]SIM[/green]" if r.get("arquivo_existe") else "[red]NÃO[/red]"
        erro_txt = str(r.get("erro") or "-")
        tempo_txt = f"{r.get('tempo_execucao_segundos', 0.0):.1f}s"
        tabela.add_row(
            str(r.get("ordem", 1)),
            r.get("id", "?"),
            status_badge,
            tempo_txt,
            r.get("arquivo_saida", "-"),
            existe_badge,
            erro_txt
        )
    
    console.print(tabela)

def validar_manifesto(tarefas: list):
    if not isinstance(tarefas, list) or len(tarefas) == 0:
        raise ValueError("O manifesto deve ser uma lista não vazia de tarefas.")
    
    ids_vistos = set()
    destinos_vistos = set()
    campos_esperados = {"id", "ordem", "runner", "modelo", "timeout", "prompt", "arquivo_saida"}

    for t in tarefas:
        if set(t.keys()) != campos_esperados:
            raise ValueError(f"Campos inválidos na tarefa {t.get('id', '?')}: {set(t.keys()) ^ campos_esperados}")
        if not isinstance(t["ordem"], int) or t["ordem"] < 1:
            raise ValueError(f"Ordem inválida na tarefa {t['id']}.")
        if t["runner"] not in RUNNERS_PERMITIDOS:
            raise ValueError(f"Runner '{t['runner']}' não suportado.")
        if t["modelo"] not in MODELOS_PERMITIDOS:
            raise ValueError(f"Modelo '{t['modelo']}' não permitido.")
        if not isinstance(t["timeout"], (int, float)) or t["timeout"] <= 0:
            raise ValueError(f"Timeout inválido na tarefa {t['id']}.")
        if not re.match(r"^[a-zA-Z0-9_-]+$", t["id"]):
            raise ValueError(f"ID inválido na tarefa '{t['id']}': use apenas caracteres alfanuméricos, hífen ou sublinhado.")
        if t["id"] in ids_vistos:
            raise ValueError(f"ID duplicado: {t['id']}")
        ids_vistos.add(t["id"])

        destino = Path(t["arquivo_saida"])
        if destino.is_absolute() or ".." in destino.parts:
            raise ValueError(f"Caminho inseguro detectado: {t['arquivo_saida']}")
        if str(destino) in destinos_vistos:
            raise ValueError(f"Arquivo de saída duplicado: {t['arquivo_saida']}")
        destinos_vistos.add(str(destino))

async def drenar_stream(stream, f_log, prefixo: str, cor_prefixo: str, t_id: str):
    while True:
        linha = await stream.readline()
        if not linha:
            break
        texto_dec = linha.decode('utf-8', errors='replace')
        f_log.write(f"[{prefixo}] {texto_dec}")
        f_log.flush()
        texto_limpo = texto_dec.strip()
        if texto_limpo and not texto_limpo.startswith("→ Read") and not texto_limpo.startswith("✱ Glob"):
            console.print(f"  [dim]{t_id}[/dim] [{cor_prefixo}]{prefixo}[/{cor_prefixo}] {texto_limpo}")

async def verificar_qualidade_artefato(arquivo: Path) -> tuple[bool, str | None]:
    if not arquivo.is_file() or arquivo.stat().st_size == 0:
        return False, f"Arquivo '{arquivo.name}' não foi criado ou está vazio."

    if arquivo.suffix == ".py":
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "py_compile", str(arquivo),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip()
                return False, f"Falha de compilação Python: {err_msg}"
        except Exception as e:
            return False, f"Erro ao verificar sintaxe Python: {str(e)}"

    return True, None

async def executar_tarefa(tarefa: dict, run_dir: Path, semaforo: asyncio.Semaphore) -> dict:
    t_id = tarefa["id"]
    timeout = tarefa["timeout"]
    arquivo_esperado = run_dir / tarefa["arquivo_saida"]
    arquivo_esperado.parent.mkdir(parents=True, exist_ok=True)
    log_task_name = f"log_{t_id}.log"
    log_task_path = run_dir / log_task_name

    resultado = {
        "id": t_id,
        "ordem": tarefa["ordem"],
        "status": "FALHA",
        "tempo_execucao_segundos": 0.0,
        "arquivo_saida": tarefa["arquivo_saida"],
        "arquivo_existe": False,
        "log_file": log_task_name,
        "erro": None
    }

    cmd = [
        "opencode", "run",
        "--auto",
        "-m", tarefa["modelo"],
        "--dir", str(run_dir),
        tarefa["prompt"]
    ]

    async with semaforo:
        log_evento(f"🚀 [bold cyan]Iniciando tarefa[/bold cyan] [bold yellow]{t_id}[/bold yellow] (Ordem {tarefa['ordem']}) | Modelo: [green]{tarefa['modelo']}[/green]")
        inicio = time.time()

        try:
            with open(log_task_path, "w", encoding="utf-8") as f_log:
                processo = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                t_out = asyncio.create_task(drenar_stream(processo.stdout, f_log, "STDOUT", "cyan", t_id))
                t_err = asyncio.create_task(drenar_stream(processo.stderr, f_log, "STDERR", "yellow", t_id))

                try:
                    await asyncio.wait_for(processo.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    processo.kill()
                    await asyncio.gather(t_out, t_err, return_exceptions=True)
                    resultado["erro"] = f"Timeout de {timeout}s excedido."
                    resultado["tempo_execucao_segundos"] = round(time.time() - inicio, 2)
                    log_evento(f"⏱ [bold red]TIMEOUT[/bold red] [{t_id}] após {timeout}s.")
                    return resultado

                await asyncio.gather(t_out, t_err, return_exceptions=True)

            duracao = round(time.time() - inicio, 2)
            resultado["tempo_execucao_segundos"] = duracao

            if processo.returncode != 0:
                resultado["erro"] = f"Processo encerrou com código {processo.returncode}."
                log_evento(f"❌ [bold red]ERRO[/bold red] [{t_id}]: Código {processo.returncode}")
                return resultado

            valido, erro_validacao = await verificar_qualidade_artefato(arquivo_esperado)
            if valido:
                resultado["status"] = "SUCESSO"
                resultado["arquivo_existe"] = True
                log_evento(f"✅ [bold green]SUCESSO[/bold green] [bold cyan]{t_id}[/bold cyan] em {duracao}s (Artefato validado).")
            else:
                resultado["erro"] = erro_validacao
                log_evento(f"❌ [bold red]FALHA DE VALIDAÇÃO[/bold red] [{t_id}]: {erro_validacao}")

        except Exception as e:
            resultado["erro"] = str(e)
            resultado["tempo_execucao_segundos"] = round(time.time() - inicio, 2)
            log_evento(f"💥 [bold red]EXCEÇÃO[/bold red] [{t_id}]: {str(e)}")

    return resultado

async def processar_manifesto(caminho_manifesto: Path):
    timestamp_run = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"run_{timestamp_run}_{uuid.uuid4().hex[:4]}"
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    with open(caminho_manifesto, "r", encoding="utf-8") as f:
        tarefas = json.load(f)

    with open(run_dir / "pipeline.json", "w", encoding="utf-8") as f:
        json.dump(tarefas, f, indent=2, ensure_ascii=False)

    log_evento(f"\n📦 [bold bright_white]Nova Execução Iniciada:[/bold bright_white] [bold cyan]{run_id}[/bold cyan] ({len(tarefas)} tarefas agendadas)")

    semaforo_opencode = asyncio.Semaphore(MAX_WORKERS)
    etapas = sorted(list({t["ordem"] for t in tarefas}))
    resultados_finais = []
    status_geral = "SUCESSO"

    for etapa in etapas:
        tarefas_etapa = [t for t in tarefas if t["ordem"] == etapa]
        console.rule(f"[bold yellow]⚡ Processando Etapa {etapa} ({len(tarefas_etapa)} tarefa(s) simultâneas)[/bold yellow]")

        tarefas_corrotinas = [
            executar_tarefa(t, run_dir, semaforo_opencode)
            for t in tarefas_etapa
        ]
        resultados_etapa = await asyncio.gather(*tarefas_corrotinas)
        resultados_finais.extend(resultados_etapa)

        falhas_etapa = [r for r in resultados_etapa if r["status"] != "SUCESSO"]
        if falhas_etapa:
            status_geral = "FALHA"
            log_evento(f"🛑 [bold red]Etapa {etapa} falhou ({len(falhas_etapa)} erro(s)). Pipeline interrompido.[/bold red]")
            break

    dados_saida = {
        "run_id": run_id,
        "status_geral": status_geral,
        "tarefas": resultados_finais
    }

    tmp_res = run_dir / "resultado.json.tmp"
    final_res = run_dir / "resultado.json"
    with open(tmp_res, "w", encoding="utf-8") as f:
        json.dump(dados_saida, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_res, final_res)

    exibir_tabela_resumo(run_id, status_geral, resultados_finais)
    log_evento(f"🏁 [bold bright_white]Run [{run_id}] finalizada com status:[/bold bright_white] {'[bold green]SUCESSO[/bold green]' if status_geral == 'SUCESSO' else '[bold red]FALHA[/bold red]'}\n")

async def monitorar():
    exibir_banner()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        if PIPELINE_FILE.is_file():
            manifesto_trabalho = BASE_DIR / "pipeline.json.processing"
            try:
                os.replace(PIPELINE_FILE, manifesto_trabalho)
            except OSError:
                await asyncio.sleep(0.5)
                continue

            log_evento("🎯 [bold green]Manifesto pipeline.json capturado com sucesso![/bold green]")
            try:
                with open(manifesto_trabalho, "r", encoding="utf-8") as f:
                    conteudo = json.load(f)
                validar_manifesto(conteudo)
                await processar_manifesto(manifesto_trabalho)
                manifesto_trabalho.replace(BASE_DIR / "pipeline.json.done")
            except Exception as e:
                log_evento(f"❌ [bold red]Erro ao processar manifesto:[/bold red] {str(e)}")
                manifesto_trabalho.replace(BASE_DIR / "pipeline.json.err")

        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        asyncio.run(monitorar())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Encerrado pelo usuário.[/bold yellow]")
