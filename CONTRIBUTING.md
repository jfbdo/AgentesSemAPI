# Contribuição e distribuição

Execute `bash scripts/check.sh` antes de enviar alterações. A CI usa Python 3.11–3.13 em Linux e não usa contas nem chamadas reais. Novos adaptadores devem manter logs brutos, eventos normalizados, timeout e encerramento de processos.

Para publicar esta pasta em um repositório GitHub, use o repositório que o proprietário escolher. Não há URL AgentesV2 presumida no código. Antes do push:

1. Rode a demo, testes e validate no exemplo.
2. Confira `git status` e arquivos rastreados; não inclua runs, .env, logs nem credenciais.
3. Confira README e AGENTS.md na raiz para que uma IA consiga descobrir o guia sem contexto da conversa.
4. Documente quais versões dos CLIs passaram no smoke test real. Testes simulados não substituem autenticação.
5. Ao atualizar o repositório antigo, preserve o histórico e revise o diff antes do commit/push.

O núcleo é licenciado sob MIT. Não distribua uma instalação com suas credenciais, sessões de CLI ou arquivos pessoais. Seus amigos usam as próprias contas.
