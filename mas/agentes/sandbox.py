"""Sandbox Docker dos Implementadores e o ambiente de rede em volta dele.

- DockerSandbox: backend do deepagents. Um container com JDK, Maven e Node
  (imagem SANDBOX_IMAGEM) que monta a pasta do código em /workspace. Os
  arquivos que o agente escreve caem direto no disco do host.
- AmbienteDocker: uma rede Docker por execução com os serviços do tech_stack
  (banco, cache) e o sandbox. O sandbox enxerga o banco como `db` e o cache como
  `redis`; as variáveis de conexão (SPRING_DATASOURCE_URL, DB_HOST...) já vêm
  exportadas, então o backend sobe e os testes de integração rodam sem configurar
  nada. Os dois Implementadores (backend e frontend) usam o MESMO sandbox: o do
  frontend pode subir o backend e testar as telas contra a API em localhost.
"""
import os
import posixpath
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileUploadResponse,
    FileDownloadResponse,
)

WORKSPACE = "/workspace"
# O deepagents grava resultados grandes de tools e histórico resumido nestas
# pastas na raiz do backend ("/"), que o usuário não-root do container não cria.
_PASTAS_DEEPAGENTS = ("/large_tool_results", "/conversation_history")


def _docker(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["docker", *args], check=check, capture_output=True, **kw)
    except FileNotFoundError as e:
        raise RuntimeError("Docker não encontrado no PATH (necessário para o sandbox).") from e
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"docker {args[0]} falhou: {e.stderr.decode('utf-8', errors='replace')}") from e


class DockerSandbox(BaseSandbox):

    def __init__(self, root_dir: str, *, imagem: str = "dockersandbox",
                 rede: Optional[str] = None, env: Optional[Dict[str, str]] = None,
                 portas: Sequence[int] = (5173,), nome: Optional[str] = None,
                 rotulos: Optional[Dict[str, str]] = None):
        self.host_dir = Path(root_dir).resolve()  # Resolve the root directory path
        self.host_dir.mkdir(parents=True, exist_ok=True)
        self.nome = nome or "DockerSandbox-" + uuid.uuid4().hex[:8]  # nome único
        self.imagem = imagem
        self.rede = rede
        self.env = dict(env or {})
        self.portas = list(portas)
        self.rotulos = dict(rotulos or {})

    def start(self):
        comando = [
            "run",
            "-d",
            "--rm",
            "--name", self.nome,
            "-v", f"{self.host_dir}:{WORKSPACE}",          # pasta do código aparece como /workspace
            "-w", WORKSPACE,
            "--user", f"{os.getuid()}:{os.getgid()}",      # arquivos criados ficam do usuário do host
            "-e", "HOME=/tmp/home",                        # npm/maven precisam de um HOME gravável
            "-e", "NO_COLOR=1",                            # desativa cores no output
            "-e", "MAVEN_ARGS=-B",
            "-e", "npm_config_color=false",
            "--memory", "4g", "--cpus", "2", "--pids-limit", "512",  # limites de recurso
        ]
        for porta in self.portas:                          # ex.: 5173 = front (Vite)
            comando += ["-p", f"127.0.0.1:{porta}:{porta}"]
        if self.rede:
            comando += ["--network", self.rede, "--network-alias", "app"]
        for k, v in self.env.items():
            comando += ["-e", f"{k}={v}"]
        for k, v in self.rotulos.items():
            comando += ["--label", f"{k}={v}"]
        comando += [self.imagem, "sleep", "infinity"]      # mantém o container vivo
        _docker(*comando)
        pastas = " ".join(_PASTAS_DEEPAGENTS)
        _docker("exec", "-u", "0", self.nome, "sh", "-c",
                f"mkdir -p {pastas} && chown {os.getuid()}:{os.getgid()} {pastas}")

    def stop(self):
        _docker("rm", "-f", self.nome, check=False)
        restante = _docker("ps", "-a", "--filter", f"name=^{self.nome}$",
                           "--format", "{{.Names}}", check=False)
        if restante.stdout.strip():
            raise RuntimeError(f"docker rm falhou: {restante.stdout.decode('utf-8')}")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()

    @property
    def id(self):
        return self.nome

    def caminho_no_container(self, caminho_host: Path) -> str:
        """/workspace/... correspondente a um caminho do host dentro de host_dir."""
        rel = Path(caminho_host).resolve().relative_to(self.host_dir).as_posix()
        return WORKSPACE if rel == "." else f"{WORKSPACE}/{rel}"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        limite_s = timeout or 600
        try:
            resultado = subprocess.run(
                ["docker", "exec", self.nome, "timeout", str(limite_s), "bash", "-c", command],
                capture_output=True,
                # Um processo em segundo plano que herda stdout segura o pipe aberto
                # mesmo depois do `timeout`; sem isto o pipeline travaria.
                timeout=limite_s + 30,
            )
        except subprocess.TimeoutExpired as e:
            saida = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", errors="replace")
            return ExecuteResponse(
                output=saida[-30000:] + "\n[comando não terminou: se iniciou um servidor em "
                "segundo plano, redirecione a saída: nohup cmd > /tmp/log 2>&1 &]",
                exit_code=124, truncated=len(saida) > 30000)
        saida = resultado.stdout.decode("utf-8", errors="replace") + resultado.stderr.decode("utf-8", errors="replace")

        limite = 30000
        cortou = len(saida) > limite
        if cortou:
            saida = saida[-limite:]   # guarda o FINAL, que é onde aparecem os erros de build

        return ExecuteResponse(output=saida, exit_code=resultado.returncode, truncated=cortou)


    # O modelo às vezes pede grep/glob na raiz ("/", "/.", "."), o que varre o
    # container inteiro (/usr, /proc...) e trava até o timeout de 10 min.
    # Aqui essas buscas são redirecionadas para /workspace.
    @staticmethod
    def _caminho_de_busca(path: str | None) -> str | None:
        if path is None or posixpath.normpath(path).strip("/") in ("", "."):
            return WORKSPACE
        return path

    def grep(self, pattern, path=None, glob=None, **kwargs):
        return super().grep(pattern, self._caminho_de_busca(path), glob, **kwargs)

    def glob(self, pattern, path=None):
        return super().glob(pattern, self._caminho_de_busca(path))

    @staticmethod
    def _codigo_de_erro(stderr: bytes) -> str:
        """Traduz a mensagem de erro do Linux para os códigos padrão do deepagents."""
        texto = stderr.decode("utf-8", errors="replace")
        if "No such file" in texto:
            return "file_not_found"
        if "Permission denied" in texto:
            return "permission_denied"
        if "Is a directory" in texto:
            return "is_directory"
        if "Not a directory" in texto:
            return "invalid_path"
        return texto.strip() or "erro desconhecido"

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        resultados = []
        for caminho, conteudo in files:
            try:
                q = shlex.quote(caminho)   # protege o caminho se tiver espaço ou caractere especial
                resultado = subprocess.run(
                    ["docker", "exec", "-i", self.nome, "sh", "-c",
                     f'mkdir -p "$(dirname {q})" && cat > {q}'],
                    input=conteudo,        # o conteúdo entra pela entrada padrão do cat
                    capture_output=True,
                )
                if resultado.returncode == 0:
                    resultados.append(FileUploadResponse(path=caminho, error=None))
                else:
                    resultados.append(FileUploadResponse(path=caminho, error=self._codigo_de_erro(resultado.stderr)))
            except Exception as e:          # um arquivo com problema não derruba os outros
                resultados.append(FileUploadResponse(path=caminho, error=str(e)))
        return resultados

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        resultados = []
        for caminho in paths:
            try:
                resultado = subprocess.run(
                    ["docker", "exec", self.nome, "cat", caminho],
                    capture_output=True,
                )
                if resultado.returncode == 0:
                    resultados.append(FileDownloadResponse(path=caminho, content=resultado.stdout, error=None))
                else:
                    resultados.append(FileDownloadResponse(path=caminho, content=None, error=self._codigo_de_erro(resultado.stderr)))
            except Exception as e:
                resultados.append(FileDownloadResponse(path=caminho, content=None, error=str(e)))
        return resultados


# ======================================================================
# Serviços de apoio (banco, cache) na mesma rede do sandbox
# ======================================================================
@dataclass
class Servico:
    """Container de apoio. `alias` é o hostname dele na rede."""
    nome: str                      # ex.: "PostgreSQL 16"
    alias: str                     # ex.: "db"
    imagem: str
    porta: int
    env: Dict[str, str] = field(default_factory=dict)      # do próprio container
    env_app: Dict[str, str] = field(default_factory=dict)  # exportadas no sandbox
    pronto: Optional[List[str]] = None                     # comando de prontidão
    comando: List[str] = field(default_factory=list)


_USUARIO = _SENHA = _BANCO = "app"


def _versao(v: str, padrao: str) -> str:
    v = (v or "").strip().lower().lstrip("v")
    return v if re.fullmatch(r"\d+(\.\d+){0,2}", v) and v != "0" else padrao


def _servico_banco(nome: str, versao: str) -> Optional[Servico]:
    n = nome.lower()
    cred = {"DB_HOST": "db", "DB_NAME": _BANCO, "DB_USER": _USUARIO,
            "DB_USERNAME": _USUARIO, "DB_PASSWORD": _SENHA}
    spring = {"SPRING_DATASOURCE_USERNAME": _USUARIO, "SPRING_DATASOURCE_PASSWORD": _SENHA}
    if "postgres" in n:
        url = f"jdbc:postgresql://db:5432/{_BANCO}"
        return Servico(
            f"PostgreSQL {versao}", "db", f"postgres:{_versao(versao, '16')}-alpine", 5432,
            env={"POSTGRES_DB": _BANCO, "POSTGRES_USER": _USUARIO, "POSTGRES_PASSWORD": _SENHA},
            env_app={**cred, **spring, "DB_PORT": "5432", "SPRING_DATASOURCE_URL": url,
                     "DATABASE_URL": f"postgresql://{_USUARIO}:{_SENHA}@db:5432/{_BANCO}"},
            pronto=["pg_isready", "-U", _USUARIO, "-d", _BANCO])
    if "mysql" in n or "maria" in n:
        imagem = (f"mariadb:{_versao(versao, '11')}" if "maria" in n
                  else f"mysql:{_versao(versao, '8')}")
        url = f"jdbc:{'mariadb' if 'maria' in n else 'mysql'}://db:3306/{_BANCO}"
        return Servico(
            f"{nome} {versao}", "db", imagem, 3306,
            env={"MYSQL_DATABASE": _BANCO, "MYSQL_USER": _USUARIO, "MYSQL_PASSWORD": _SENHA,
                 "MYSQL_ROOT_PASSWORD": _SENHA, "MARIADB_DATABASE": _BANCO,
                 "MARIADB_USER": _USUARIO, "MARIADB_PASSWORD": _SENHA,
                 "MARIADB_ROOT_PASSWORD": _SENHA},
            env_app={**cred, **spring, "DB_PORT": "3306", "SPRING_DATASOURCE_URL": url,
                     "DATABASE_URL": f"mysql://{_USUARIO}:{_SENHA}@db:3306/{_BANCO}"})
    if "mongo" in n:
        uri = f"mongodb://db:27017/{_BANCO}"
        return Servico(f"MongoDB {versao}", "db", f"mongo:{_versao(versao, '7')}", 27017,
                       env_app={"DB_HOST": "db", "DB_PORT": "27017", "DB_NAME": _BANCO,
                                "MONGODB_URI": uri, "SPRING_DATA_MONGODB_URI": uri})
    return None


def _servico_cache(nome: str, versao: str) -> Optional[Servico]:
    if "redis" not in nome.lower():
        return None
    return Servico(
        f"Redis {versao}", "redis", f"redis:{_versao(versao, '7')}-alpine", 6379,
        env_app={"REDIS_HOST": "redis", "REDIS_PORT": "6379", "REDIS_URL": "redis://redis:6379",
                 "SPRING_DATA_REDIS_HOST": "redis", "SPRING_DATA_REDIS_PORT": "6379"},
        pronto=["redis-cli", "ping"])


def servicos_do_contexto(contexto) -> List[Servico]:
    """Serviços de apoio exigidos pelo tech_stack do PROJECT CONTEXT."""
    ts = contexto.tech_stack
    candidatos = [_servico_banco(ts.database.name, ts.database.version),
                  _servico_cache(ts.cache.name, ts.cache.version)]
    return [s for s in candidatos if s]


class AmbienteDocker:
    """Rede + serviços de apoio + sandbox, criados e destruídos juntos.

    Uso:
        with AmbienteDocker(codigo_dir, servicos_do_contexto(ctx)) as amb:
            agente = criar_agente(..., amb.sandbox)
    Um serviço que não sobe (ex.: imagem indisponível) não impede o resto: fica
    fora de `amb.ativos` e da descrição passada ao agente.
    """

    def __init__(self, root_dir: str, servicos: Sequence[Servico] = (), *,
                 imagem: str = "dockersandbox", portas: Sequence[int] = (5173,),
                 espera_s: float = 60):
        self.id = "mas-" + uuid.uuid4().hex[:8]
        self.rede = self.id
        self.servicos = list(servicos)
        self.ativos: List[Servico] = []
        self.espera_s = espera_s
        self._containers: List[str] = []
        self._rotulos = {"mas.ambiente": self.id}
        self._imagem, self._portas, self._root = imagem, portas, root_dir
        self.sandbox: Optional[DockerSandbox] = None

    def start(self) -> "AmbienteDocker":
        _docker("network", "create", "--label", f"mas.ambiente={self.id}", self.rede)
        try:
            for s in self.servicos:
                if self._subir(s):
                    self.ativos.append(s)
            env = {k: v for s in self.ativos for k, v in s.env_app.items()}
            self.sandbox = DockerSandbox(
                self._root, imagem=self._imagem, rede=self.rede, env=env,
                portas=self._portas, nome=f"{self.id}-app", rotulos=self._rotulos)
            self.sandbox.start()
            self._containers.append(self.sandbox.nome)
            self._aguardar_prontos()
        except BaseException:
            self.stop()
            raise
        return self

    def _subir(self, s: Servico) -> bool:
        nome = f"{self.id}-{s.alias}"
        cmd = ["run", "-d", "--rm", "--name", nome, "--network", self.rede,
               "--network-alias", s.alias, "--label", f"mas.ambiente={self.id}"]
        for k, v in s.env.items():
            cmd += ["-e", f"{k}={v}"]
        try:
            _docker(*cmd, s.imagem, *s.comando, timeout=600)  # inclui o pull da imagem
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            print(f"  [sandbox] AVISO: {s.nome} não subiu ({s.imagem}): {e}")
            return False
        self._containers.append(nome)
        return True

    def _aguardar_prontos(self) -> None:
        limite = time.time() + self.espera_s
        for s in self.ativos:
            if not s.pronto:
                continue
            while _docker("exec", f"{self.id}-{s.alias}", *s.pronto, check=False).returncode:
                if time.time() > limite:
                    print(f"  [sandbox] AVISO: {s.nome} não respondeu em {self.espera_s:.0f}s")
                    break
                time.sleep(1)

    def stop(self) -> None:
        for nome in reversed(self._containers):
            _docker("rm", "-f", nome, check=False)
        self._containers.clear()
        _docker("network", "rm", self.rede, check=False)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop()

    def descricao(self) -> str:
        """Texto para o agente: o que há na rede e como conectar."""
        linhas = [f"- {s.nome}: host '{s.alias}', porta {s.porta}" for s in self.ativos]
        if any(s.alias == "db" for s in self.ativos):
            linhas.append(f"  credenciais do banco: usuário '{_USUARIO}', senha '{_SENHA}', "
                          f"banco '{_BANCO}'")
        env = sorted({k for s in self.ativos for k in s.env_app})
        if env:
            linhas.append("- Variáveis já exportadas no sandbox: " + ", ".join(env))
        faltaram = [s.nome for s in self.servicos if s not in self.ativos]
        if faltaram:
            linhas.append(f"- Indisponíveis nesta execução: {', '.join(faltaram)} "
                          "(não dependa deles nos testes)")
        return "\n".join(linhas) or "- (nenhum serviço de apoio)"
