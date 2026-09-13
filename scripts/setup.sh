#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -c 'import sys; assert sys.version_info >= (3,11), "Instale Python 3.11 ou superior"'
python3 -m agentes doctor
python3 -m unittest discover -s tests -v
printf '\nPronto para demonstração: python3 -m agentes demo\n'
printf 'Com tmux: python3 -m agentes demo --dashboard\n'
