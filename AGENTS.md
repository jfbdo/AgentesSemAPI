# Instruções para IAs — instalação e manutenção

## Objetivo e escopo

Este repositório fornece um sistema pronto; instale e configure o existente antes de propor reescritas. Use somente login de assinatura nos CLIs oficiais. Não peça chaves de API, não copie tokens, não use cookies de navegador, proxies de credenciais ou endpoints privados.

A preferência do usuário é instalação autônoma das partes reversíveis e diagnóstico claro. A autenticação pessoal no navegador/MFA deve ser concluída pelo usuário; não é possível automatizar essa identidade apenas recebendo o link GitHub. Não diga que o modelo funciona antes de um teste real.

## Roteiro para instalar na máquina de alguém

1. Leia README.md e docs/INSTALACAO.md. Identifique sistema, shell, Python, git, tmux, Codex e Antigravity (`agy`) disponíveis. Não leia arquivos de autenticação.
2. Clone a URL efetivamente fornecida pelo usuário; não invente uma URL AgentesV2. Preserve arquivos existentes e configurações pessoais.
3. Prefira `./iniciar.sh` para conduzir o usuário pelo menu, sem exigir comandos de plano/execução. Use Python 3.11+ em Linux/macOS/WSL2. Execute `bash scripts/setup.sh`. A demo funciona sem dependências Python, sem privilégios e sem internet.
4. Execute `python3 -m agentes demo`. Confira `state.json`, `result.json` e as três pastas de artefatos. Todos devem terminar em succeeded.
5. Instale tmux pelo gerenciador da máquina se autorizado. Execute `python3 -m agentes demo --dashboard --demo-delay 4`. Confirme o coordenador no primeiro painel e os agentes paginados; a revisão deve aguardar as duas dependências. Não mate outras sessões tmux.
6. Para provedores reais, instale CLIs oficiais segundo a documentação vigente. Gemini CLI não atende mais contas individuais; use Antigravity CLI. Verifique `codex exec --help` e `agy --help` contra agentes/runners.py.
7. Oriente login ChatGPT no Codex e login Google no Antigravity com a conta AI Pro/Ultra. Nunca solicite senha/token em chat. Evite reautenticar contas já conectadas.
8. Confira `agy --sandbox` e não use `--dangerously-skip-permissions` para contornar falhas.
9. Use examples/equipe.json para um teste pequeno com modelos reais quando o usuário quiser testar suas assinaturas. Relate separadamente demo, testes automatizados e smoke test autenticado. Se faltar um provedor, não declare teste completo.
10. Entregue comandos com o caminho real da execução, forma de parar/retomar, dependências pendentes e localização das entregas.

## Desenvolvimento

- Arquitetura: manifest.py (contrato), runners.py (CLIs/eventos), engine.py (DAG/retomada), storage.py (estado/lock), monitor.py (leitura/tmux), cli.py (comandos).
- Nenhuma API paga ou credencial deve ser adicionada. Nunca remova sandbox para “resolver” erros silenciosamente.
- O plano pode ser escrito por outra IA. Valide tipos, ciclos, caminhos e critérios antes de executar. Saídas não podem ser links simbólicos.
- Alterações nos eventos devem preservar compatibilidade de leitores e manter logs brutos separados. Nunca mostrar raciocínio interno como “atividade”.
- Testes: `bash scripts/check.sh`. Use mocks/runner demo em CI; nunca contas reais ou secrets no GitHub Actions.
- Um agente por tarefa com pasta por tentativa. Dependências recebem cópias de artefatos. Não introduza escrita concorrente em uma árvore compartilhada sem desenhar integração e conflitos.
- Atualize documentação e exemplos sempre que mudar o contrato. Não edite plan.json de uma execução existente; crie outra execução.
- Não publique, faça push, nem crie repositório remoto a menos que o usuário peça. Antes de publicar, revise arquivos rastreados; runs e logs ficam locais.
