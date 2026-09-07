# AgentesSemAPI

Eu criei este projeto para ter vários agentes de IA trabalhando em equipe no meu computador, sem precisar gastar uma fortuna com créditos de API.

A ideia é simples: uso o **OpenCode CLI** autenticado com minha conta do **ChatGPT Plus via OAuth** e um pequeno orquestrador em Python. Assim, consigo executar vários agentes ao mesmo tempo com os melhores modelos da OpenAI disponíveis na minha assinatura, sem pagar uma API separada.

> Na prática, não é IA “sem custo”: é IA sem cobrança adicional de API, usando uma assinatura do ChatGPT que eu já tenho. Os limites de uso da conta continuam valendo.

## 💡 O que este projeto faz

Eu descrevo o que quero construir, o trabalho é dividido em tarefas e cada tarefa é entregue a um agente. Os agentes podem criar código, documentação, testes e outros arquivos.

O projeto cuida automaticamente de:

- organizar a fila de tarefas;
- executar até 3 agentes em paralelo por padrão;
- respeitar a ordem entre as etapas;
- interromper o pipeline se uma etapa falhar;
- salvar cada execução, arquivo e log em uma pasta separada;
- verificar se o arquivo pedido foi criado;
- validar a sintaxe de arquivos Python gerados.

## ⚙️ Como funciona

O fluxo tem apenas três peças:

1. **Antigravity / Gemini planeja**

   Eu explico o objetivo, e ele divide o trabalho em tarefas pequenas e gera o arquivo `pipeline.json`.

2. **`orchestrator.py` organiza**

   Esse é o motorzinho assíncrono que fica rodando em segundo plano. Ele encontra o `pipeline.json`, valida as tarefas, controla a fila e o paralelismo e mostra um painel visual no terminal.

3. **OpenCode CLI executa**

   Para cada tarefa, o orquestrador chama o OpenCode com o modelo GPT escolhido. O agente trabalha em uma pasta isolada e entrega o arquivo pronto. Quando o resultado é Python, o orquestrador também confere a sintaxe.

```text
Antigravity / Gemini
        ↓ cria
   pipeline.json
        ↓ lido por
  orchestrator.py
        ↓ chama
   OpenCode CLI
        ↓ entrega
 arquivos + logs + resultado.json
```

## 🚀 Como eu rodo em 4 passos

### 1. Preparo os pré-requisitos

Eu uso:

- Linux ou macOS;
- Python 3.10 ou mais recente;
- uma assinatura ativa do ChatGPT Plus, Team ou Enterprise;
- [OpenCode CLI](https://opencode.ai) instalado.

Se ainda não tiver o OpenCode:

```bash
curl -fsSL https://opencode.ai/install | bash
```

Depois de clonar o repositório, entro na pasta:

```bash
git clone <url-do-repositorio>
cd AgentesSemAPI
```

### 2. Instalo a dependência Python

```bash
pip install -r requirements.txt
```

A única dependência atual é o `rich`, usado para montar o painel no terminal.

### 3. Conecto o OpenCode ao ChatGPT

```bash
opencode providers login
```

Escolho **OpenAI** e concluo o login no navegador. A autenticação é feita por OAuth; eu não coloco chave de API no projeto.

### 4. Inicio o monitor

```bash
./start_monitor.sh
```

Quando o painel mostrar `ONLINE`, está pronto. Agora só preciso colocar um `pipeline.json` na raiz do projeto.

## 📝 Exemplo de tarefa

Este exemplo pede para um agente criar uma pequena calculadora em Python:

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

Cada campo tem uma função bem direta:

| Campo | O que significa |
| --- | --- |
| `id` | Um nome único para eu reconhecer a tarefa. |
| `ordem` | A etapa da tarefa. Tarefas com a mesma ordem rodam juntas. |
| `runner` | O executor. Neste projeto, uso sempre `opencode`. |
| `modelo` | O modelo GPT que vai executar o trabalho. |
| `timeout` | Quanto tempo o agente pode usar, em segundos. |
| `prompt` | A instrução completa do que deve ser feito. |
| `arquivo_saida` | O arquivo que precisa existir quando a tarefa terminar. |

Para evitar que o monitor leia um JSON ainda incompleto, eu primeiro salvo como `pipeline.json.tmp` e depois renomeio:

```bash
mv pipeline.json.tmp pipeline.json
```

O orquestrador detecta o arquivo automaticamente. Ao terminar, encontro tudo em:

```text
runs/run_<data>_<id>/
├── pipeline.json
├── resultado.json
├── log_criar_calculadora.log
└── calculadora.py
```

### Paralelismo sem complicação

Se eu colocar três tarefas com `"ordem": 1`, as três podem rodar ao mesmo tempo. Uma tarefa com `"ordem": 2` só começa depois que todas as tarefas da etapa 1 terminarem com sucesso.

Por padrão, o projeto usa até 3 agentes simultâneos. Se eu quiser mudar esse número:

```bash
MAX_WORKERS=5 ./start_monitor.sh
```

## 🛡️ Segurança

Eu deixei o repositório preparado para ser publicado sem expor minhas credenciais:

- não existem chaves de API gravadas no código;
- o login da OpenAI é feito pelo próprio OpenCode via OAuth;
- o `.gitignore` bloqueia arquivos `.env`, tokens, chaves, logs e dados de autenticação;
- a pasta `runs/` e os arquivos temporários do pipeline não entram no Git;
- caminhos de saída absolutos ou contendo `..` são rejeitados pelo orquestrador.

Mesmo com essas proteções, eu sempre reviso o `git status` antes de publicar qualquer alteração. Credenciais, tokens e arquivos pessoais nunca devem ser adicionados manualmente ao repositório.

## Estrutura rápida

```text
AgentesSemAPI/
├── orchestrator.py      # gerencia tarefas, etapas e agentes
├── start_monitor.sh     # inicia o painel com reinício automático
├── requirements.txt     # dependências Python
├── pipeline.json        # tarefas que eu quero executar
├── runs/                # resultados e logs locais
└── .gitignore           # impede o versionamento de dados sensíveis
```

É isso: eu inicio o monitor, gero um `pipeline.json` e deixo os agentes trabalharem.
