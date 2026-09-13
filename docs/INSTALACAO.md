# Instalação assistida ou manual

## 1. Requisitos e clonagem

Sistemas compatíveis: **Linux, macOS (Apple Silicon e Intel) ou WSL2**; Python 3.11+; Git para clonar; tmux para os painéis interativos. O núcleo do projeto utiliza exclusivamente a biblioteca padrão do Python. Windows nativo não é compatível (locks de concorrência e processos usam POSIX): no Windows, use WSL2.

```bash
git clone https://github.com/jfbdo/AgentesSemAPI.git
cd AgentesSemAPI
bash scripts/setup.sh
python3 -m agentes demo
```

Não é preciso rodar como root.

### Instalação de pré-requisitos do sistema:

- **Ubuntu / Debian / WSL2:**
  ```bash
  sudo apt-get update
  sudo apt-get install python3 git tmux
  ```
- **macOS (via Homebrew):**
  ```bash
  brew install python git tmux
  ```

Opcionalmente, para ter o comando `agentes` globalmente via virtualenv:
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
agentes doctor
```

---

## 2. Painéis e Dashboard

```bash
python3 -m agentes demo --dashboard --demo-delay 4
```

O comando inicia o motor em segundo plano e abre uma sessão tmux exclusiva da execução. Cada página mantém o coordenador no topo e mostra os agentes em painéis lado a lado.
- `Alt + Setas` ou clique para selecionar um agente.
- `Ctrl + ←/→` para paginar entre agentes.
- `↑/↓` ou roda do mouse para rolar o painel selecionado.
- `t` para trocar o modelo de um agente em tempo real.
- `p` para pausar ou retomar um agente.
- `F1` para abrir a tela flutuante de ajuda com todos os atalhos.
- `Ctrl + R` para reconstruir janelas e painéis se algum for fechado.
- `Ctrl + B`, depois `z` amplia o painel selecionado.
- `Ctrl + B`, depois `d` desconecta do dashboard sem encerrar o trabalho dos agentes.

---

## 3. CLIs Oficiais dos Provedores (Modo Assinatura)

O projeto opera exclusivamente através dos CLIs oficiais usando a autenticação da sua assinatura pessoal (sem chaves de API):

### 🟢 OpenAI Codex (`codex`)
- **Instalação**:
  ```bash
  npm install -g @openai/codex
  ```
- **Login**:
  ```bash
  codex login
  ```
  Escolha o login com sua conta ChatGPT.

### 🔵 Google Antigravity (`agy`)
- **Instalação**:
  ```bash
  curl -fsSL https://antigravity.google/cli/install.sh | bash
  ```
- **Login**:
  ```bash
  agy
  ```
  Conecte com sua conta Google AI Pro ou Ultra. O adaptador utiliza sandbox ativado e modo headless sem abrir prompts invasivos.

### 🟣 Anthropic Claude Code (`claude`)
- **Instalação**:
  ```bash
  npm install -g @anthropic-ai/claude-code
  ```
- **Login**:
  ```bash
  claude auth login
  ```
  Conecte com sua conta Claude Pro ou Claude Team.

### 🟡 OpenCode (`opencode`)
- **Instalação**:
  ```bash
  curl -fsSL https://opencode.ai/install.sh | bash
  ```
- **Configuração**:
  ```bash
  opencode providers
  ```
  Permite selecionar modelos livres e abertos com rotação comunitária sem custo.

---

## 4. Variáveis Bloqueadas (Garantia Sem API Paga)

Para garantir que nenhuma cobrança acidental por token de API ocorra, as seguintes variáveis de ambiente são **terminantemente rejeitadas** pelo executor:
- `OPENAI_API_KEY`
- `CODEX_API_KEY`
- `GEMINI_API_KEY`
- `GOOGLE_API_KEY`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_GENAI_USE_VERTEXAI`
- `ANTHROPIC_API_KEY`

Se qualquer uma dessas variáveis estiver presente no seu terminal, o sistema recusa a execução e solicita que você a remova da sessão atual antes de prosseguir.

---

## 5. Testes e Validação do Ambiente

Para validar se o seu ambiente e ferramentas estão operacionais:

```bash
# Diagnóstico completo das ferramentas e modo assinatura
python3 -m agentes doctor

# Executar a bateria de testes automatizados
bash scripts/check.sh

# Validar plano de exemplo
python3 -m agentes validate examples/equipe.json
```
