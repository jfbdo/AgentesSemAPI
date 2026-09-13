# Solução de problemas

| Sintoma | Ação |
|---|---|
| No module named agentes | Entre na pasta clonada ou instale o pacote no ambiente Python ativo |
| Python antigo / fcntl ausente | Use Python 3.11+ em Linux/macOS/WSL2 |
| tmux não instalado | Instale pelo gerenciador do sistema ou use watch em terminais separados |
| Não vejo todos os agentes | Use Ctrl+← ou Ctrl+→ para passar as páginas; o coordenador aparece em todas elas |
| As setas rolam o agente errado | Primeiro selecione o painel com Alt+setas ou clique nele; depois use ↑/↓ ou a roda do mouse |
| Texto técnico muito grande | O painel mostra somente o resumo; instruções completas ficam em plan.json e eventos técnicos nos logs |
| Antigravity mostra ajuda e `flags provided but not defined: -m` | Atualize o projeto; o Antigravity exige `--model` |
| Painel fechado por engano | Pressione Ctrl+R no dashboard ou execute `python3 -m agentes dashboard <run> --rebuild` |
| Como ver todos os atalhos | Pressione F1 para abrir a janela de ajuda flutuante do sistema |
| Agente demorando ou cota esgotada | Pressione `t` para trocar o modelo/provedor em tempo real, ou `p` para pausar/retomar |
| CLI não encontrado | Execute doctor e confira PATH no shell que inicia o coordenador |
| Erro de login | Abra o CLI oficial interativamente e confirme a conta; nunca cole tokens no chat |
| Claude CLI não encontrado | Instale com `npm install -g @anthropic-ai/claude-code` e faça login com `claude auth login` |
| Gemini CLI recusa conta individual | Comportamento esperado após 18/06/2026; abra o menu atualizado e use Antigravity (`agy`) |
| Antigravity sandbox falhou | Confira `agy --help` e os diagnósticos; não use `--dangerously-skip-permissions` para contornar |
| Ferramenta negada no Antigravity | O modo headless pode recusar ações que exigem aprovação; ajuste o escopo ou a política oficial |
| Antigravity termina sem criar a saída | O projeto configura `proceed-in-sandbox` e mantém `--sandbox`; atualize e retome a atividade |
| PAUSADO por cota | Pressione `t` para migrar para outro modelo/provedor ou execute `python3 -m agentes switch <run> --to <provedor>` |
| Tempo limite | Inspecione logs; para alterar timeout, crie um novo plano/execução |
| Artefato ausente | Leia a resposta do agente: outputs exige arquivo real, não somente texto em chat |
| Agente não encontrou meu relatório | Inicie uma nova atividade e use a opção de anexar arquivos; eles aparecerão em inputs/usuario |
| Revisão reprovada | Confira revisao.json e envie novo feedback ao coordenador ou use a ação direta |
| Coordenador já ativo | Não execute resume simultâneo para a mesma pasta; use watch ou stop |
| EXECUTANDO após queda abrupta | Confira processos filhos deixados pela queda; então resume. O monitor mostra o último snapshot, não uma prova de processo vivo |
| Falha imediata em background | Leia coordinator.log; o comando de lançamento confirma despacho, não sucesso da execução |
| Artefato alterado | Preserve a execução para auditoria e crie outra; a retomada recusa entradas modificadas |

## Onde investigar

1. `python3 -m agentes watch runs/ID --once`: progresso geral.
2. `state.json`: erro e pasta da tentativa por agente.
3. `events.jsonl`: ações normalizadas.
4. `logs/<id>-<tentativa>.jsonl`: saída bruta (campo raw) com stdout/stderr.
5. `coordinator.log`: erro de inicialização/execução em segundo plano.

Para compartilhar um bug, envie versões de Python/CLIs/SO, plano mínimo e erro sanitizado. Não envie runs inteiro, auth.json, tokens, chaves ou conteúdo privado de projetos.

`stop` envia SIGTERM ao coordenador ativo; aguarde ele salvar estado antes de retomar. `resume` preserva saídas concluídas e inicia novas tentativas para as demais. Não existe monitoramento automático permanente de cota: a retomada é solicitada pelo usuário.
