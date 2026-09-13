# Contrato do plano v2

Veja [examples/equipe.json](../examples/equipe.json), que pode ser copiado por qualquer IA. JSON UTF-8, sem comentários. O comando `validate` não inicia modelos.

```json
{
  "version": 2,
  "objective": "Criar uma função de soma",
  "tasks": [
    {
      "id": "backend",
      "role": "Desenvolvedor Python",
      "summary": "Criar uma função de soma em Python.",
      "stage": "Implementação",
      "runner": "codex",
      "prompt": "Crie soma.py com somar(a, b).",
      "depends_on": [],
      "outputs": ["soma.py"],
      "checks": [{"kind": "python", "path": "soma.py"}],
      "timeout": 600,
      "retries": 1
    }
  ]
}
```

## Campos

| Campo | Regra |
|---|---|
| version | Inteiro 2 |
| objective | Objetivo geral não vazio |
| tasks | 1 a 100 tarefas |
| id | Único, 1–60 letras ASCII, números, _ ou -. coordinator é reservado |
| role | Papel descritivo do agente |
| summary | Opcional: frase de até 140 caracteres mostrada nos painéis |
| stage | Nome de etapa mostrado no monitor; não determina dependências |
| runner | codex, gemini, opencode, claude ou demo. `gemini` usa o Antigravity CLI (`agy`) nas contas individuais |
| model | Opcional: identificador aceito pelo CLI; omitir usa padrão do cliente |
| prompt | Instrução completa da tarefa |
| depends_on | IDs das dependências diretas; padrão [] |
| outputs | Lista não vazia de arquivos relativos obrigatórios |
| checks | Critérios automáticos; padrão [] |
| timeout | Inteiro de 1 a 86400 segundos; padrão 600 |
| retries | Tentativas adicionais automáticas, de 0 a 3; padrão 0 |

Campos desconhecidos, IDs duplicados, ciclos e dependências inexistentes são rejeitados. Pastas de saída não podem ter '..', componentes vazios, caminho absoluto ou nomes reservados. Arquivos devem ser não vazios, regulares, sem links simbólicos e de no máximo 20 MiB cada. `outputs` não aceita glob nem diretório inteiro.

## Dependências e integração

O escalonamento segue depends_on, não a ordem da lista nem o nome da etapa. Etapas podem se sobrepor: o painel mostra todas as etapas ativas.

Cada tarefa tem uma pasta nova por tentativa. Arquivos anexados no assistente ficam em `inputs/usuario/` para todos os agentes. Se depende de backend, verá os arquivos publicados por backend em `inputs/backend/`. Recebe somente saídas de dependências diretas; para acessar a entrega de outro agente, inclua-o em depends_on. Entradas são cópias, portanto edições não afetam o produtor.

Declare uma tarefa integradora com todas as dependências relevantes e saídas finais em sua própria pasta. Não há merge automático entre repositórios nem acesso implícito aos arquivos do projeto que chamou o CLI. O assistente aceita arquivos regulares de até 50 MiB; importar uma árvore inteira de projeto ainda não é suportado.

Agentes podem ter o mesmo nome de arquivo em outputs porque cada um publica em `artifacts/<id>/`. Para corrigir código existente, o agente seguinte deve ler a entrada e escrever a versão corrigida como sua própria saída.

## Critérios automáticos

Todos os outputs são verificados quanto a existência/tamanho. Checks adicionais:

| kind | Verificação |
|---|---|
| python | Parse de sintaxe com ast.parse; não executa o código |
| json | Parse JSON; não valida estrutura de domínio |
| contains | Exige substring literal informada em text |
| json_equals | Exige campo no primeiro nível igual a value, incluindo tipo |
| plan | Valida um plano completo v2; usado pelo coordenador planejador |

Exemplo de revisão que bloqueia a entrega se a IA a reprovar:

```json
{"kind":"json_equals","path":"revisao.json","field":"approved","value":true}
```

O revisor deve explicar suas evidências no arquivo. Um booleano de aprovação não garante correção; os critérios devem refletir o problema. Para testes funcionais, peça ao agente executor/revisor que os execute sob seu sandbox e entregue evidências. Não há execução de comandos arbitrários de checks pelo processo Python do coordenador nesta versão.

## Falhas e retomada

Falha bloqueia seus descendentes; tarefas independentes podem continuar. Erro reconhecido de autenticação ou cota pausa a abertura de novas tarefas, sem cancelar as já ativas. Uma tarefa falha pode repetir automaticamente até retries vezes em novas pastas, recebendo o erro anterior no prompt. Cota e autenticação não repetem automaticamente.

`resume` reexecuta apenas tarefas não concluídas; o orçamento de retries é renovado nessa retomada manual. Não repita indiscriminadamente tarefas com efeitos externos. Saídas já concluídas são conferidas por hash. Planos não devem ser editados dentro de runs: gere uma nova execução para mudar instruções ou dependências.
