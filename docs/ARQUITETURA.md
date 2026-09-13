# Arquitetura e limites

## Módulos

| Módulo | Responsabilidade |
|---|---|
| manifest.py | Validação de tipos, caminhos e DAG |
| storage.py | JSON atômico, eventos e lock exclusivo POSIX |
| runners.py | Comandos Codex/Antigravity/OpenCode/Claude/demo, streams e término de processos |
| engine.py | Agendamento, artefatos, tentativas, migração de cota e retomada |
| monitor.py | Leitores independentes, dashboard interativo e sessões tmux |
| review.py | Avaliação de feedback pós-execução, rodadas cíclicas e ação direta |
| ui.py | Sistema de design de terminal (cards, tabelas Unicode e badges) |
| wizard.py | Assistente interativo guiado e catálogo de modelos |
| cli.py | Interface pública, diagnóstico e comandos |

O processo coordenador é o único escritor do estado. Cada evento atualiza state.json por rename atômico, e events.jsonl guarda histórico. Leitores toleram uma última linha incompleta. Um lock de arquivo impede dois motores na mesma execução. Execuções diferentes são independentes e os limites de concorrência NÃO são globais entre elas.

O plano persiste o modelo escolhido em cada tarefa. O assistente obtém os modelos Gemini com `agy models` e os modelos Codex do catálogo público local do cliente; o coordenador só pode selecionar IDs presentes nesses catálogos. O painel mostra essa decisão antes e durante a execução.

O motor usa asyncio, barreiras de dependência e limites global/por provedor. Os CLIs são subprocessos sem shell intermediário, com grupos de processos próprios. Timeout cobre envio de prompt, espera e drenagem dos streams. Encerramento normal, timeout e cancelamento terminam o grupo de processos. Um desligamento abrupto/SIGKILL do coordenador não garante encerrar filhos; verifique-os antes de retomar após crash do sistema.

Snapshots persistem progresso por tarefa. Entradas são cópias de artefatos validados; publicação ocorre em staging seguida de rename. SHA-256 detecta alterações entre execução e retomada. Isso não é proteção contra um invasor local com acesso aos arquivos.

## Monitoramento

São consumidores somente de leitura. O tmux não hospeda os processos dos modelos: pode ser desconectado ou encerrado sem interromper o motor. Uma sessão por execução permite reconectar. O layout pagina os agentes, repete o coordenador no primeiro painel e limita cada página a quatro agentes, com capacidade adaptada ao tamanho do terminal.

Normalizamos eventos observáveis dos CLIs. Eventos desconhecidos e mensagens de sistema continuam nos logs brutos; reasoning não entra no painel. Mensagens são quebradas por largura do terminal e o conteúdo pode ser rolado com setas/PgUp/PgDn. O painel mostra até 30 eventos recentes da janela de leitura; o histórico completo continua nos logs. Os arquivos de log ainda podem conter informação sensível produzida pela tarefa e nunca devem ser publicados automaticamente.

## Isolamento e autenticação

- Pastas individuais evitam colisões acidentais; não são barreira de segurança.
- Codex usa workspace-write e exige login ChatGPT via config por invocação; não recebe API key.
- O runner `gemini` usa o Antigravity CLI com sandbox, sem ignorar permissões. Autenticação precisa estar configurada no cliente oficial.
- Antigravity pode carregar configurações e plugins do usuário. Revise-os no CLI; o orquestrador não extrai tokens nem inspeciona arquivos de credenciais.
- Variáveis comuns de API/Vertex são recusadas. Isso não é uma auditoria de toda configuração possível dos clientes nem garantia comercial sobre futuras versões.
- Código de saída, eventos de erro e artefatos determinam o resultado. Um evento de erro conservadoramente reprova a tentativa mesmo com exit code zero; consulte o bruto se o CLI tiver recuperado um erro internamente.
- Classificação de erros quota/auth é heurística. O usuário retoma explicitamente após resolver o problema; nenhum fallback pago é implementado.

## Fora do escopo atual

Sem interface web, autenticação compartilhada, serviço multiusuário, distribuição remota, merge de worktrees ou memória de chat entre tentativas. Também não há garantia de execução exatamente uma vez em efeitos externos, replanejamento autônomo ou medição de porcentagem de cota. Planejamento gera um JSON revisável antes da execução.

O roadmap recomendado é importar projetos existentes com worktrees, testes funcionais declarativos sob sandbox e posterior dashboard web sobre o mesmo contrato de eventos. A implementação atual deliberadamente não precisa de servidor web nem banco de dados.
