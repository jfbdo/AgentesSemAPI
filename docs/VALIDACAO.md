# Verificação desta entrega

Verificado em 12/09/2026, Linux com Python 3.12.3.

- 21 testes automatizados passaram: contrato do plano, ciclos, caminhos, retomada, imutabilidade do plano, hashes de artefatos, limite de concorrência, falhas/dependências, cota, retries, cancelamento, revisão negativa, eventos e término de subprocessos filhos.
- Demonstração offline: três tarefas concluídas, entradas copiadas entre dependências e artefatos publicados.
- Integração por CLI: iniciar em background, solicitar stop e retomar até concluir.
- tmux: sessão verificada com coordenador fixo no primeiro painel e agentes distribuídos em páginas adaptáveis de até quatro.
- Codex instalado neste ambiente: 0.154.0-alpha.6.2; flags do adaptador conferidas contra exec --help.
- Gemini CLI, Node/npm e runtime de sandbox não estavam instalados neste ambiente.
- Chamadas reais autenticadas ao Codex/Gemini não foram executadas. Portanto, esta entrega não certifica login, cotas, modelos ou sandbox da conta de cada usuário.

Reproduzir: `bash scripts/check.sh` e `python3 -m agentes demo`. Depois siga docs/INSTALACAO.md para o smoke test autenticado. A CI configurada testa Python 3.11, 3.12 e 3.13; essas três versões não foram todas executadas localmente nesta entrega.
