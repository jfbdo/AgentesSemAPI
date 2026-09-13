"""User-owned runtime installation; never modifies system npm prefix or shell files."""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def tools_root():
    return Path.home()/'.local/share/agentessemapi/tools'


def activate():
    root = tools_root()
    directories = [str(root/'node/bin'), str(root/'npm/bin')]
    rest = [p for p in os.environ.get('PATH','').split(os.pathsep) if p not in directories]
    os.environ['PATH'] = os.pathsep.join(directories + rest)


def node_major():
    if not shutil.which('node'):
        return 0
    try:
        result = subprocess.run(['node','--version'], capture_output=True, text=True, timeout=10, check=True)
        return int(result.stdout.strip().lstrip('v').split('.')[0])
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def select_release(releases, system, arch):
    candidates = [r for r in releases if r.get('lts') and
                  re.fullmatch(r'v\d+\.\d+\.\d+', r.get('version','')) and
                  int(r['version'].split('.')[0][1:]) >= 22 and
                  (f'{system}-{arch}' if system == 'linux' else f'osx-{arch}-tar') in r.get('files',[])]
    if not candidates:
        raise RuntimeError('Não foi encontrado Node LTS compatível com este sistema.')
    return max(candidates,key=lambda r:tuple(map(int,r['version'][1:].split('.'))))['version']


def ensure_node():
    activate()
    if node_major() >= 22 and shutil.which('npm'):
        return
    system = {'Linux':'linux','Darwin':'darwin'}.get(platform.system())
    arch = {'x86_64':'x64','aarch64':'arm64','arm64':'arm64'}.get(platform.machine())
    if not system or not arch:
        raise RuntimeError('Instalação automática Node suporta Linux/macOS x64/arm64.')
    version = select_release(json.loads(fetch('https://nodejs.org/dist/index.json')),system,arch)
    name = f'node-{version}-{system}-{arch}'
    filename = name + '.tar.gz'
    print(f'📦 Instalando Node {version} para este usuário; o Node do sistema será preservado.', flush=True)
    sums = fetch(f'https://nodejs.org/dist/{version}/SHASUMS256.txt').decode()
    expected = next((line.split()[0] for line in sums.splitlines() if line.split()[-1] == filename), None)
    if not expected:
        raise RuntimeError('Checksum oficial do Node ausente.')
    archive = fetch(f'https://nodejs.org/dist/{version}/{filename}')
    if hashlib.sha256(archive).hexdigest() != expected:
        raise RuntimeError('Download do Node não passou na verificação SHA-256.')
    root = tools_root()
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        path = Path(tmp)/filename
        path.write_bytes(archive)
        with tarfile.open(path) as tar:
            tar.extractall(tmp,filter='data')
        target = root/'node'
        if target.exists():
            shutil.rmtree(target)
        os.replace(Path(tmp)/name,target)
    activate()
    if node_major() < 22:
        raise RuntimeError('Node instalado, mas não conseguiu iniciar nesta máquina.')


def install_clients():
    if not shutil.which('codex'):
        ensure_node()
        prefix = tools_root()/'npm'
        prefix.mkdir(parents=True,exist_ok=True)
        print('📦 Instalando @openai/codex na pasta do usuário…', flush=True)
        subprocess.run(['npm','install','--global','--prefix',str(prefix),
                        '--engine-strict','@openai/codex'],check=True)
        activate()
    if not shutil.which('agy'):
        print('📦 Instalando o Antigravity CLI oficial na pasta do usuário…', flush=True)
        installer = fetch('https://antigravity.google/cli/install.sh')
        local_bin = Path.home()/'.local/bin'
        local_bin.mkdir(parents=True, exist_ok=True)
        subprocess.run(['bash', '-s', '--', '--dir', str(local_bin)],
                       input=installer, check=True)
    if shutil.which('agy'):
        subprocess.run(['agy','--version'],check=True,timeout=60)
    print('✅ Clientes instalados. Agora conecte suas contas pelo menu.',flush=True)


def install_claude_cli():
    """Install Claude Code CLI into user tools directory."""
    if not shutil.which('claude'):
        ensure_node()
        prefix = tools_root()/'npm'
        prefix.mkdir(parents=True, exist_ok=True)
        print('📦 Instalando @anthropic-ai/claude-code na pasta do usuário…', flush=True)
        subprocess.run(['npm', 'install', '--global', '--prefix', str(prefix),
                        '--engine-strict', '@anthropic-ai/claude-code'], check=True)
        activate()


def prepare_antigravity_sandbox(settings_path=None, workspace=None):
    """Allow unattended commands only inside Antigravity's OS sandbox."""
    path = Path(settings_path) if settings_path else Path.home()/'.gemini/antigravity-cli/settings.json'
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'Configuração Antigravity inválida: {path}') from exc
        if not isinstance(settings, dict):
            raise RuntimeError(f'Configuração Antigravity deve ser um objeto JSON: {path}')
    else:
        settings = {}
    changed = not (settings.get('enableTerminalSandbox') is True and
                   settings.get('toolPermission') == 'proceed-in-sandbox')
    settings['enableTerminalSandbox'] = True
    settings['toolPermission'] = 'proceed-in-sandbox'
    if workspace is not None:
        workspace = Path(workspace).resolve()
        run_root = next((parent.parent for parent in (workspace, *workspace.parents)
                         if parent.name == 'work'), workspace)
        permissions = settings.setdefault('permissions', {})
        if not isinstance(permissions, dict):
            raise RuntimeError('permissions deve ser um objeto na configuração Antigravity')
        allowed = permissions.setdefault('allow', [])
        if not isinstance(allowed, list):
            raise RuntimeError('permissions.allow deve ser uma lista na configuração Antigravity')
        for rule in (f'read_file({run_root})', f'write_file({run_root})'):
            if rule not in allowed:
                allowed.append(rule)
                changed = True
    if not changed:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return True


def install_system():
    packages = []
    if not shutil.which('tmux'):
        packages.append('tmux')
    if not packages:
        print('✅ tmux encontrado. O sandbox será conferido pelo Antigravity CLI.')
        return
    if shutil.which('brew'):
        print('📦 Instalando ' + ', '.join(packages) + ' via Homebrew…')
        subprocess.run(['brew', 'install', *packages], check=True)
        return
    if not shutil.which('apt-get'):
        print('Instalação do runtime por este menu disponível via apt-get ou brew. Veja docs/INSTALACAO.md.')
        return
    print('Instalando ' + ', '.join(packages) + '. A senha administrativa, se necessária, é digitada somente no terminal.')
    subprocess.run(['sudo','apt-get','install','-y',*packages],check=True)
