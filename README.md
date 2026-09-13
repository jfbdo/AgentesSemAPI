# AgentesSemAPI

> **Coordenação autônoma de equipes de agentes de IA locais, sem chaves de API pagas e sem cobranças por token.**

O **AgentesSemAPI** executa projetos e atividades complexas divididas entre múltiplos agentes de IA especializados trabalhando em paralelo e em rodadas sequenciais, utilizando **exclusivamente o login das assinaturas que você já possui** nos CLIs oficiais dos provedores:

- 🟢 **OpenAI Codex CLI** (`codex`): Modelos GPT via assinatura ChatGPT Plus/Pro.
- 🔵 **Google Antigravity CLI** (`agy`): Modelos Gemini via assinatura Google AI Pro/Ultra.
- 🟣 **Anthropic Claude Code CLI** (`claude`): Modelos Claude via assinatura Claude Pro/Team.
- 🟡 **OpenCode CLI** (`opencode`): Modelos abertos, livres e de rotação comunitária sem custo.

Cada agente possui seu próprio monitor visual com progresso em tempo real, mantendo os logs brutos e de depuração estritamente isolados.

---

## ⚡ Início Rápido (Demonstração Offline)

A demonstração funciona imediatamente **sem internet, sem contas, sem chaves e sem consumir cotas**:

```bash
git clone https://github.com/jfbdo/AgentesSemAPI.git
cd AgentesSemAPI
./iniciar.sh
```

Escolha a opção **`2 • Experimentar / Demonstração`** para ver o coordenador e os agentes especialistas executando em tempo real.

---

## 🧭 Menu Interativo (`./iniciar.sh`)

O script `./iniciar.sh` (ou `python3 -m agentes`) apresenta a interface principal do sistema:

```text
╭──────────────────────────────────────────────────────────────────╮
│  🧭 AGENTES SEM API — Assistente de Atividades                   │
│  Coordenação autônoma com múltiplos agentes, modelos e rodadas   │
╰──────────────────────────────────────────────────────────────────╯

╭─ Menu Principal ─────────────────────────────────────────────────╮
   1 │ 🚀 Nova atividade real            (utiliza suas contas conectadas)
   2 │ 🧪 Experimentar / Demonstração    (sem consumir assinaturas)
   3 │ ⚙️  Configurar / Conectar contas  (diagnóstico e credenciais)
   4 │ 📋 Acompanhar / Retomar           (atividades anteriores)
   0 │ 🚪 Sair                           
╰──────────────────────────────────────────────────────────────────╯
› Escolha [1]:
```

### Funcionalidades do Menu:
1. **Nova atividade real**: Informe o objetivo da sua tarefa em linguagem natural, anexe arquivos de referência (via seletor de arquivos ou terminal) e defina o tamanho da equipe (1 a 8 agentes). O coordenador IA planeja as etapas, escolhe os modelos ideais e você pode revisar/trocar os modelos antes de iniciar.
2. **Experimentar**: Executa simulações completas offline para validar o fluxo e layout dos painéis.
3. **Configurar / Conectar contas**: Diagnóstico completo do ambiente (`doctor`) e atalhos diretos para autenticar suas contas de assinatura no navegador (`codex login`, `agy`, `claude auth login`, `opencode providers`).
4. **Acompanhar / Retomar**: Listagem com status de todas as execuções anteriores, permitindo reconectar ao dashboard, reiniciar do zero, pausar ou migrar modelos.

---

## 🖥️ Dashboard Interativo & Atalhos

Durante a execução no `tmux`, o dashboard divide a tela entre o Coordenador e os Agentes Executores com controles em tempo real:

| Tecla / Atalho | Ação |
| :--- | :--- |
| **`Alt + Setas`** ou **Clique** | Seleciona o agente desejado no dashboard. |
| **`Ctrl + ← / →`** | Alterna entre as páginas de agentes (quando houver mais de 4 agentes). |
| **`t`** | **Trocar modelo em tempo real**: Permite selecionar outro modelo/provedor com cota liberada a qualquer momento, mesmo enquanto o agente estiver executando. |
| **`p`** | **Pausar / Retomar**: Pausa a execução do agente selecionado imediatamente; pressionar novamente retoma o trabalho. |
| **`F1`** | **Ajuda do Sistema**: Abre uma janela flutuante com a lista completa de atalhos e comandos sem interferir nos monitores. |
| **`Ctrl + R`** | **Restaurar Layout**: Reconstrói as janelas e painéis do dashboard caso algum monitor seja fechado acidentalmente. |
| **`o`** | **Abrir Pasta de Entregas**: Abre a pasta de artefatos finais diretamente no gerenciador de arquivos do sistema (Nautilus, Dolphin, Finder no Mac). |
| **`Ctrl + B, z`** | Maximiza/restaura o painel do agente selecionado no tmux. |
| **`Ctrl + B, d`** | Desconecta do dashboard (o trabalho dos agentes continua rodando em segundo plano). |

---

## 🔄 Revisão do Coordenador & Ação Direta

Ao término de uma atividade, o sistema oferece um ciclo de **revisão orientada por IA**:
- Você pode analisar as entregas e enviar feedbacks, correções ou pedidos de melhoria.
- **Ação Direta Automática**: Se você solicitar mover ou copiar os documentos gerados para sua pasta pessoal (ex.: `~/Downloads` ou `~/Desktop`), o coordenador executa a transferência diretamente pelo sistema sem reiniciar a fila de agentes.
- **Rodadas Cíclicas & Debates**: Se você solicitar debates ou refinamentos sucessivos (ex.: "faça 3 rodadas de revisão entre o programador e o auditor"), o coordenador encadeia novas tarefas onde cada agente aprimora a entrega do anterior.

---

## 🔒 Segurança & Modo Assinatura Estrito

Este projeto segue rigorosamente o princípio de **autonomia sem credenciais privadas**:
- **Zero chaves de API**: Nenhuma variável como `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` ou `GEMINI_API_KEY` é permitida. Se detectadas no ambiente, o sistema recusa a execução para evitar consumo acidental de créditos pagos de API.
- **Autenticação Pessoal Segura**: Todo login é realizado exclusivamente pelo navegador oficial do respectivo CLI (`OAuth/MFA`). O projeto **nunca** lê, extrai, armazena ou trafega tokens de acesso.
- **Isolamento de Sandbox**: Cada agente opera em sua própria subpasta de trabalho e com sandboxing ativado nos CLIs oficiais, impedindo alterações não autorizadas em arquivos fora do escopo da atividade.

---

## 💻 Compatibilidade Multiplataforma

O **AgentesSemAPI** é compatível nativamente com:
- **Linux** (Ubuntu, Debian, Fedora, Arch, etc.)
- **macOS** (Apple Silicon M1/M2/M3/M4 e processadores Intel)
- **WSL2** (Windows Subsystem for Linux)

### Pré-requisitos:
- **Python 3.11+** (utiliza apenas módulos da biblioteca padrão do Python).
- **Git**
- **tmux** (para o dashboard multipainel):
  - Ubuntu/Debian: `sudo apt-get install tmux`
  - macOS (Homebrew): `brew install tmux`

---

## 📚 Documentação Adicional

- [Guia de Instalação e Configuração de Contas](docs/INSTALACAO.md)
- [Arquitetura do Motor DAG e Isolamento de Tentativas](docs/ARQUITETURA.md)
- [Especificação do Manifesto de Planos](docs/PLANOS.md)
- [Solução de Problemas e Diagnóstico](docs/TROUBLESHOOTING.md)
- [Instruções para IAs Autônomas](AGENTS.md)
- [Diretrizes de Contribuição](CONTRIBUTING.md)

---

## 📄 Licença

Distribuído sob a licença [MIT](LICENSE).
