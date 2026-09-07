#!/usr/bin/env bash
# ==============================================================================
# Script de Inicialização Rápida: Monitor Visual do Orquestrador Multiagente
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "  🚀 Iniciando Orquestrador Multiagente Local     "
echo "=================================================="

# Verifica se o Python 3 está instalado
if ! command -v python3 &> /dev/null; then
    echo "❌ Erro: python3 não encontrado no sistema."
    exit 1
fi

# Inicia o orchestrator com auto-restart em caso de encerramento acidental
while true; do
    python3 orchestrator.py
    echo ""
    echo "⚠️  Orquestrador finalizado. Reiniciando em 3 segundos... (Pressione Ctrl+C para encerrar definitivamente)"
    sleep 3
done
