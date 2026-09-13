# Migração do AgentesSemAPI anterior

Esta é uma estrutura nova para evolução do projeto que usava Antigravity -> pipeline.json -> OpenCode. Não há conversão automática de manifests antigos nem vigilância de um arquivo fixo na raiz.

| Anterior | v2 |
|---|---|
| Lista JSON na raiz | Objeto com version=2, objective e tasks |
| ordem | depends_on explícito; stage é rótulo visual |
| runner=opencode | runner=codex ou gemini; `gemini` executa pelo Antigravity CLI |
| modelo | model opcional com ID do CLI escolhido |
| arquivo_saida | outputs, lista de arquivos |
| Um diretório por execução | Um diretório por tarefa e tentativa |
| Console misturado | Coordenador + monitor por tarefa |
| start_monitor.sh | python3 -m agentes run PLANO --dashboard |

Para preservar semântica de etapas, cada tarefa de uma ordem deve depender dos IDs de todas as tarefas da ordem imediatamente anterior. Depois simplifique dependências quando forem independentes.

Prompts antigos que leem arquivos diretamente da pasta compartilhada precisam usar inputs/<dependência>/<arquivo>. Uma tarefa integradora produz a entrega final. Modelos OpenCode com prefixo openai/ não devem ser copiados cegamente para Codex; omita model inicialmente.

O Antigravity ou outra IA pode continuar produzindo o plano seguindo docs/PLANOS.md. O comando plan é uma alternativa local. Não mova credenciais do OpenCode para os outros clientes: faça login oficial em cada um.
