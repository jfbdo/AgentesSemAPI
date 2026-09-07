# AgentesSemAPI

O **AgentesSemAPI** é um projeto desenvolvido para executar múltiplos agentes de IA trabalhando em equipe, diretamente no seu computador.

A proposta é simples: aproveitar uma assinatura do ChatGPT Plus por meio do **OpenCode CLI**, autenticado via OAuth, para executar tarefas com modelos GPT sem cobrança por token de API. Um orquestrador em Python organiza as etapas, controla os agentes e reúne os resultados de cada execução.

> **Importante:** o projeto evita cobranças adicionais de API, mas depende de uma assinatura ativa do ChatGPT Plus. Os limites e as regras de uso da conta continuam válidos.

## O que o projeto faz

A partir de um objetivo, o trabalho é dividido em tarefas que podem criar código, documentação, testes e outros arquivos. O AgentesSemAPI cuida de todo o fluxo:

- organiza as tarefas por etapas;
- executa até três agentes em paralelo por padrão;
- aguarda a conclusão de uma etapa antes de iniciar a próxima;
- interrompe o pipeline quando uma etapa falha;
- armazena arquivos e logs em uma pasta exclusiva para cada execução;
- confirma se o arquivo solicitado foi criado e não está vazio;
- valida a sintaxe de arquivos Python gerados.

## Como funciona

O fluxo é formado por três componentes principais:

### 1. Antigravity / Gemini planeja

O objetivo do projeto é informado ao Antigravity/Gemini, que divide o trabalho em tarefas menores e gera o arquivo `pipeline.json`.

### 2. `orchestrator.py` gerencia

O orquestrador monitora o `pipeline.json`, valida sua estrutura, organiza as etapas e controla o paralelismo. Durante a execução, um painel visual no terminal mostra o andamento e o resultado das tarefas.

### 3. OpenCode CLI executa

Cada tarefa é enviada ao OpenCode CLI com o modelo GPT definido no pipeline. O agente trabalha dentro da pasta da execução, cria o arquivo solicitado e registra sua atividade. Ao final, o orquestrador verifica o artefato gerado e, no caso de arquivos Python, também valida a sintaxe.

```text
Antigravity / Gemini
        |
        v
   pipeline.json
        |
        v
  orchestrator.py
        |
        v
   OpenCode CLI
        |
        v
arquivos + logs + resultado.json
```

## Instalação e execução

### 1. Verifique os pré-requisitos

Antes de começar, tenha instalado:

- Linux ou macOS;
- Python 3.10 ou mais recente;
- uma assinatura ativa do ChatGPT Plus;
- [OpenCode CLI](https://opencode.ai).

Para instalar o OpenCode CLI:

```bash
curl -fsSL https://opencode.ai/install | bash
```

Clone o repositório e acesse a pasta do projeto:

```bash
git clone <url-do-repositorio>
cd AgentesSemAPI
```

### 2. Instale a dependência Python

```bash
pip install -r requirements.txt
```

A dependência atual é o `rich`, responsável pelo painel visual exibido no terminal.

### 3. Conecte o OpenCode ao ChatGPT

```bash
opencode providers login
```

Selecione **OpenAI** e conclua a autenticação no navegador. O acesso é feito via OAuth, sem a necessidade de adicionar uma chave de API ao projeto.

### 4. Inicie o monitor

```bash
./start_monitor.sh
```

Quando o painel mostrar o status `ONLINE`, o orquestrador estará pronto. Para iniciar um trabalho, basta disponibilizar um arquivo `pipeline.json` na raiz do projeto.

## Exemplo de `pipeline.json`

O exemplo abaixo solicita a criação de uma calculadora simples em Python:

```json
[
  {
    "id": "criar_calculadora",
    "ordem": 1,
    "runner": "opencode",
    "modelo": "openai/gpt-5.6-sol",
    "timeout": 180,
    "prompt": "Crie uma calculadora de linha de comando em Python, com soma, subtração, multiplicação e divisão. Salve o código em calculadora.py e teste antes de concluir.",
    "arquivo_saida": "calculadora.py"
  }
]
```

### O que significa cada campo

| Campo | Descrição |
| --- | --- |
| `id` | Nome único da tarefa. Use letras, números, hífen ou sublinhado. |
| `ordem` | Etapa em que a tarefa será executada. Tarefas com a mesma ordem podem rodar juntas. |
| `runner` | Executor da tarefa. Atualmente, o valor aceito é `opencode`. |
| `modelo` | Modelo GPT que executará o trabalho. |
| `timeout` | Tempo máximo da tarefa, em segundos. |
| `prompt` | Instrução completa para o agente. |
| `arquivo_saida` | Arquivo que deve existir ao final da tarefa. |

Para impedir que o monitor capture um JSON ainda incompleto, prepare o conteúdo com um nome temporário e renomeie o arquivo quando estiver pronto:

```bash
mv pipeline.json.tmp pipeline.json
```

O arquivo será detectado automaticamente. Ao final, os resultados ficarão em uma estrutura semelhante a esta:

```text
runs/run_<data>_<id>/
|-- pipeline.json
|-- resultado.json
|-- log_criar_calculadora.log
`-- calculadora.py
```

## Etapas e paralelismo

Tarefas com o mesmo valor em `ordem` pertencem à mesma etapa e podem ser executadas em paralelo. Por exemplo, três tarefas com `"ordem": 1` podem começar juntas. Uma tarefa com `"ordem": 2` só será iniciada quando todas as tarefas da etapa anterior terminarem com sucesso.

O limite padrão é de três agentes simultâneos. Para alterar esse valor, defina `MAX_WORKERS` ao iniciar o monitor:

```bash
MAX_WORKERS=5 ./start_monitor.sh
```

## Segurança

O projeto foi estruturado para reduzir o risco de exposição de credenciais e manter cada execução organizada:

- nenhuma chave de API é armazenada no código;
- a autenticação da OpenAI é realizada pelo OpenCode via OAuth;
- o `.gitignore` exclui arquivos `.env`, tokens, chaves, logs e dados de autenticação;
- a pasta `runs/` e os arquivos temporários do pipeline não são versionados;
- cada execução recebe uma pasta própria dentro de `runs/`, funcionando como sandbox para seus arquivos e logs;
- caminhos de saída absolutos ou contendo `..` são rejeitados pelo orquestrador.

Mesmo com essas proteções, é recomendável revisar `git status` antes de publicar alterações. Credenciais, tokens e arquivos pessoais nunca devem ser adicionados manualmente ao repositório.

## Estrutura do projeto

```text
AgentesSemAPI/
|-- orchestrator.py     # gerencia tarefas, etapas e agentes
|-- start_monitor.sh    # inicia o monitor com reinício automático
|-- requirements.txt    # dependências Python
|-- pipeline.json       # define as tarefas da próxima execução
|-- runs/               # armazena resultados e logs localmente
`-- .gitignore          # evita o versionamento de dados sensíveis
```

Com o monitor ativo e o `pipeline.json` disponível, o restante do fluxo é automático: as tarefas são organizadas, executadas e validadas, e os resultados ficam registrados na pasta da execução.
