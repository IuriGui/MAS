"""Compila o código gerado e traduz o log em erros estruturados.

Usado pela etapa Build (correcao.py): os agentes recebem estes erros, corrigem os
próprios arquivos e, quando a causa está num arquivo de outro dono, pedem a ele.

  backend   mvn compile                     -> erros do javac, do POM e de dependência
  frontend  npm install, tsc --noEmit, vite build

Compila numa cópia temporária, para não deixar target/ e node_modules/ na pasta
gerada. Os node_modules ficam em cache por package.json (~/.cache/tcc-mas), e o
npm roda com --ignore-scripts: o package.json é código gerado, não confiável.

Uso:
  python build.py saida/impl_f3/sistema/codigo
  python build.py <codigo> --lado backend --modulos Users,Catalog,Inventory
  python build.py <codigo> --json saida/impl_f3/build.json
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from ambiente import encurtar_tipos
from layout import slugify

TIMEOUT_S = 900
CACHE_NODE = Path.home() / ".cache" / "tcc-mas" / "node_modules"
MAX_DETALHE = 6  # linhas de detalhe guardadas por erro (o javac lista cada construtor)


@dataclass
class Erro:
    lado: str                    # backend | frontend
    tipo: str                    # compilacao | pom | dependencia | build | ferramenta
    mensagem: str
    arquivo: str = ""            # relativo à raiz do lado, como no plano
    linha: Optional[int] = None
    coluna: Optional[int] = None
    codigo: str = ""             # TS2304...
    detalhe: str = ""            # linhas seguintes do compilador (required/found...)

    def chave(self):
        return (self.lado, self.arquivo, self.linha, self.coluna, self.mensagem)

    @property
    def bloqueante(self) -> bool:
        """Erro que impede o compilador de chegar aos demais (sintaxe, pacote que
        não existe, POM, dependência): corrigido, outros erros podem aparecer."""
        return (self.tipo in ("pom", "dependencia", "build")
                or bool(_SINTAXE.search(self.mensagem) or _PACOTE.search(self.mensagem)))


@dataclass
class Resultado:
    lado: str
    ok: bool
    passos: List[str] = field(default_factory=list)  # comandos executados
    segundos: float = 0.0
    erros: List[Erro] = field(default_factory=list)
    log: str = ""                                     # final do log do passo que falhou

    @property
    def falha_de_ambiente(self) -> bool:
        """A falha é da máquina (ferramenta ausente, timeout), não do código."""
        return not self.ok and bool(self.erros) and all(e.tipo == "ferramenta"
                                                        for e in self.erros)


# ------------------------------------------------------------- leitura dos logs
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_JAVAC = re.compile(r"^\[ERROR\] (?P<arq>\S.*?\.java):\[(?P<l>\d+),(?P<c>\d+)\] (?P<msg>.*)$")
_POM = re.compile(r"^\[ERROR\]\s+(?:\[ERROR\]\s+)?(?P<msg>.+?) @ line (?P<l>\d+), column (?P<c>\d+)")
_GOAL = re.compile(r"^\[ERROR\] Failed to execute goal (?P<msg>.*?)(?: -> \[Help \d+\])?$")
_TSC = re.compile(r"^(?P<arq>[^\s(][^(]*)\((?P<l>\d+),(?P<c>\d+)\): error (?P<cod>TS\d+): (?P<msg>.*)$")
_TSC_GERAL = re.compile(r"^error (?P<cod>TS\d+): (?P<msg>.*)$")
_SINTAXE = re.compile(r"expected$|illegal start|unclosed|reached end of file|"
                      r"class, interface, enum, or record expected|not a statement")
_PACOTE = re.compile(r"^package [\w.]+ does not exist$")


def _relativo(caminho: str, raiz: Optional[str]) -> str:
    caminho = caminho.strip()
    if raiz:
        raiz = raiz.rstrip("/") + "/"
        if caminho.startswith(raiz):
            return caminho[len(raiz):]
    return caminho


def _sem_repetidos(erros: Iterable[Erro]) -> List[Erro]:
    vistos, saida = set(), []
    for e in erros:
        if e.chave() not in vistos:
            vistos.add(e.chave())
            saida.append(e)
    return saida


def erros_maven(log: str, raiz: Optional[str] = None) -> List[Erro]:
    """Erros do javac, do POM e do Maven. O Maven repete os erros do javac no
    resumo final ("Failed to execute goal ..."); as repetições são descartadas."""
    erros: List[Erro] = []
    atual: Optional[Erro] = None
    for bruta in _ANSI.sub("", log).splitlines():
        if atual is not None and bruta[:1] in (" ", "\t") and bruta.strip():
            if atual.detalhe.count("\n") < MAX_DETALHE - 1:
                atual.detalhe = (atual.detalhe + "\n" + bruta.strip()).strip()
            continue
        atual = None
        if m := _JAVAC.match(bruta):
            atual = Erro("backend", "compilacao", m["msg"].strip(), _relativo(m["arq"], raiz),
                         int(m["l"]), int(m["c"]))
            erros.append(atual)
        elif m := _POM.match(bruta):
            erros.append(Erro("backend", "pom", m["msg"].strip(), "pom.xml",
                              int(m["l"]), int(m["c"])))
        elif m := _GOAL.match(bruta):
            msg = m["msg"]
            if "Compilation failure" in msg or "compilation failure" in msg:
                continue  # os erros do javac já vieram linha a linha
            tipo = "dependencia" if re.search(r"resolve dependencies|Could not find artifact|"
                                              r"Could not transfer artifact", msg) else "build"
            erros.append(Erro("backend", tipo, _relativo(msg, raiz)))
    return _sem_repetidos(erros)


def erros_tsc(log: str) -> List[Erro]:
    erros: List[Erro] = []
    atual: Optional[Erro] = None
    for bruta in _ANSI.sub("", log).splitlines():
        if atual is not None and bruta[:1] == " " and bruta.strip():
            if atual.detalhe.count("\n") < MAX_DETALHE - 1:
                atual.detalhe = (atual.detalhe + "\n" + bruta.strip()).strip()
            continue
        atual = None
        if m := _TSC.match(bruta):
            atual = Erro("frontend", "compilacao", m["msg"].strip(), m["arq"].strip(),
                         int(m["l"]), int(m["c"]), m["cod"])
            erros.append(atual)
        elif m := _TSC_GERAL.match(bruta):
            atual = Erro("frontend", "compilacao", m["msg"].strip(), codigo=m["cod"])
            erros.append(atual)
    return _sem_repetidos(erros)


_VITE_LOCAL = re.compile(r'from "(?P<arq>[^"]+)"|^(?P<arq2>\S+\.\w+):(?P<l>\d+):(?P<c>\d+)')


def erros_vite(log: str, raiz: Optional[str] = None) -> List[Erro]:
    """O vite build só roda com o tsc limpo; pega o que o tsc não vê (imports de
    arquivos que não existem, CSS, config). Uma falha por execução."""
    texto = _ANSI.sub("", log)
    if "error during build" not in texto:
        return []
    corpo = texto.split("error during build", 1)[1].lstrip(":\n ")
    linhas = [l for l in corpo.splitlines() if l.strip() and not l.lstrip().startswith("at ")]
    msg = _relativo(linhas[0], raiz) if linhas else "vite build failed"
    if raiz:
        msg = msg.replace(raiz.rstrip("/") + "/", "")
    erro = Erro("frontend", "build", msg)
    if m := _VITE_LOCAL.search(msg):
        erro.arquivo = m["arq"] or m["arq2"]
        if m["l"]:
            erro.linha, erro.coluna = int(m["l"]), int(m["c"])
    return [erro]


def erros_npm(log: str) -> List[Erro]:
    msgs = [l[len("npm error "):].strip() for l in _ANSI.sub("", log).splitlines()
            if l.startswith("npm error ") and "A complete log" not in l]
    return [Erro("frontend", "dependencia", " | ".join(msgs[:4]) or "npm install failed",
                 "package.json")]


# ------------------------------------------------------------- execução
def _rodar(cmd: Sequence[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    env = dict(os.environ, CI="true", NO_COLOR="1", FORCE_COLOR="0")
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=env)


def _final(texto: str, linhas: int = 60) -> str:
    return "\n".join(texto.splitlines()[-linhas:])


def _falha_ferramenta(res: Resultado, lado: str, cmd: Sequence[str], exc: Exception) -> Resultado:
    if isinstance(exc, subprocess.TimeoutExpired):
        msg = f"'{' '.join(cmd)}' timed out after {exc.timeout:.0f}s"
    else:
        msg = f"'{cmd[0]}' is not available on this machine ({exc})"
    res.ok = False
    res.erros.append(Erro(lado, "ferramenta", msg))
    return res


def _sobrepor(copia: Path, alteracoes: Optional[Dict[str, str]]) -> None:
    """Grava na cópia o conteúdo proposto (caminho relativo -> conteúdo)."""
    for caminho, conteudo in (alteracoes or {}).items():
        destino = copia / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(conteudo, encoding="utf-8")


def compilar_backend(pasta: Path, offline: bool = False, timeout: int = TIMEOUT_S,
                     alteracoes: Optional[Dict[str, str]] = None) -> Resultado:
    res = Resultado("backend", ok=False)
    if not (pasta / "pom.xml").exists():
        res.erros.append(Erro("backend", "build", "pom.xml not found at the backend root",
                              "pom.xml"))
        return res
    inicio = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mvn_") as tmp:
        copia = Path(tmp) / "backend"
        shutil.copytree(pasta, copia, ignore=shutil.ignore_patterns("target"))
        _sobrepor(copia, alteracoes)
        cmd = ["mvn", "-B", "-Dstyle.color=never", "-DskipTests", "compile"]
        if offline:
            cmd.insert(1, "-o")
        res.passos.append(" ".join(cmd))
        try:
            proc = _rodar(cmd, copia, timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return _falha_ferramenta(res, "backend", cmd, exc)
        finally:
            res.segundos = round(time.monotonic() - inicio, 1)
        log = proc.stdout + proc.stderr
        res.ok = proc.returncode == 0
        if not res.ok:
            res.erros = erros_maven(log, str(copia))
            res.log = _final(log.replace(str(copia) + "/", ""))
            if not res.erros:
                res.erros.append(Erro("backend", "build", f"mvn exited with code {proc.returncode}"))
    return res


def _node_modules(package_json: Path, timeout: int) -> tuple[Optional[Path], str]:
    """node_modules instalados uma vez por conteúdo de package.json."""
    chave = hashlib.sha256(package_json.read_bytes()).hexdigest()[:16]
    destino = CACHE_NODE / chave
    if (destino / "node_modules" / ".package-lock.json").exists():
        return destino / "node_modules", ""
    destino.mkdir(parents=True, exist_ok=True)
    shutil.copy(package_json, destino / "package.json")
    cmd = ["npm", "install", "--no-audit", "--no-fund", "--ignore-scripts"]
    proc = _rodar(cmd, destino, timeout)
    if proc.returncode != 0:
        shutil.rmtree(destino, ignore_errors=True)
        return None, proc.stdout + proc.stderr
    return destino / "node_modules", ""


def compilar_frontend(pasta: Path, timeout: int = TIMEOUT_S, vite: bool = True,
                      alteracoes: Optional[Dict[str, str]] = None) -> Resultado:
    res = Resultado("frontend", ok=False)
    if not (pasta / "package.json").exists():
        res.erros.append(Erro("frontend", "build", "package.json not found at the frontend root",
                              "package.json"))
        return res
    inicio = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="tsc_") as tmp:
            copia = Path(tmp) / "frontend"
            shutil.copytree(pasta, copia, ignore=shutil.ignore_patterns("node_modules", "dist"))
            _sobrepor(copia, alteracoes)

            res.passos.append("npm install --ignore-scripts")
            try:
                modulos, log_npm = _node_modules(copia / "package.json", timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return _falha_ferramenta(res, "frontend", ["npm", "install"], exc)
            if modulos is None:
                res.erros = erros_npm(log_npm)
                res.log = _final(log_npm)
                return res
            (copia / "node_modules").symlink_to(modulos, target_is_directory=True)

            tsc = modulos / ".bin" / "tsc"
            if not tsc.exists():
                res.erros.append(Erro("frontend", "dependencia",
                                      "typescript is not in package.json devDependencies",
                                      "package.json"))
                return res
            cmd = [str(tsc), "--noEmit", "--pretty", "false"]
            res.passos.append("tsc --noEmit")
            proc = _rodar(cmd, copia, timeout)
            if proc.returncode != 0:
                log = proc.stdout + proc.stderr
                res.erros = erros_tsc(log) or [Erro("frontend", "build",
                                                    f"tsc exited with code {proc.returncode}")]
                res.log = _final(log)
                return res

            viteb = modulos / ".bin" / "vite"
            if vite and viteb.exists():
                cmd = [str(viteb), "build", "--logLevel", "error"]
                res.passos.append("vite build")
                proc = _rodar(cmd, copia, timeout)
                if proc.returncode != 0:
                    log = proc.stdout + proc.stderr
                    res.erros = erros_vite(log, str(copia)) or [
                        Erro("frontend", "build", f"vite build exited with code {proc.returncode}")]
                    res.log = _final(log.replace(str(copia) + "/", ""))
                    return res
            res.ok = True
            return res
    except subprocess.TimeoutExpired as exc:
        return _falha_ferramenta(res, "frontend", exc.cmd, exc)
    finally:
        res.segundos = round(time.monotonic() - inicio, 1)


def compilar(codigo: Path, lados: Iterable[str] = ("backend", "frontend"),
             offline: bool = False, timeout: int = TIMEOUT_S,
             alteracoes: Optional[Dict[str, Dict[str, str]]] = None) -> List[Resultado]:
    """Compila cada lado presente em <codigo>/backend e <codigo>/frontend.
    `alteracoes`: {lado: {caminho: conteúdo}} aplicadas só na cópia compilada."""
    resultados = []
    alteracoes = alteracoes or {}
    for lado in lados:
        pasta = Path(codigo) / lado
        if not pasta.exists():
            continue
        if lado == "backend":
            resultados.append(compilar_backend(pasta, offline, timeout, alteracoes.get(lado)))
        else:
            resultados.append(compilar_frontend(pasta, timeout,
                                                alteracoes=alteracoes.get(lado)))
    return resultados


# ------------------------------------------------------------- quem é o dono do erro
def dono(arquivo: str, modulos: Iterable[str]) -> str:
    """Módulo dono do arquivo, pelo segmento do caminho (pacote Java ou
    src/modules/<modulo>/); o que não é de nenhum módulo é da base."""
    slugs = {slugify(m).replace("-", "_"): m for m in modulos}
    slugs.update({slugify(m): m for m in modulos})
    for parte in Path(arquivo).parts:
        if parte in slugs:
            return slugs[parte]
    return "base"


def por_modulo(resultados: Iterable[Resultado], modulos: Iterable[str]) -> Dict[str, List[Erro]]:
    modulos = list(modulos)
    grupos: Dict[str, List[Erro]] = {}
    for r in resultados:
        for e in r.erros:
            grupos.setdefault(dono(e.arquivo, modulos), []).append(e)
    return grupos


# ------------------------------------------------------------- mensagem para o agente
def _linha(e: Erro) -> str:
    pos = f"L{e.linha}:{e.coluna} " if e.linha else ""
    cod = f"{e.codigo} " if e.codigo else ""
    texto = f"  {pos}{cod}{encurtar_tipos(e.mensagem)}"
    if e.detalhe:
        texto += "".join(f"\n      {encurtar_tipos(d)}" for d in e.detalhe.splitlines())
    return texto


def para_agente(resultados: Iterable[Resultado], arquivos: Optional[Iterable[str]] = None,
                limite: int = 40) -> str:
    """Relatório em inglês, agrupado por arquivo. Com `arquivos`, mostra só os
    erros nesses arquivos e conta os demais."""
    filtro = set(arquivos) if arquivos is not None else None
    blocos = []
    for r in resultados:
        passos = " && ".join(r.passos) or r.lado
        if r.ok:
            blocos.append(f"BUILD OK - {r.lado} ({passos}, {r.segundos:.0f}s)")
            continue
        if r.falha_de_ambiente:
            blocos.append(f"BUILD NOT RUN - {r.lado}: environment problem, not your code.\n"
                          + "\n".join(f"  {e.mensagem}" for e in r.erros))
            continue
        meus = [e for e in r.erros if filtro is None or e.arquivo in filtro]
        outros = len(r.erros) - len(meus)
        n_arq = len({e.arquivo for e in r.erros})
        cab = f"BUILD FAILED - {r.lado} ({passos}): {len(r.erros)} error(s) in {n_arq} file(s)"
        if filtro is not None:
            cab += f"; {len(meus)} in your files, {outros} elsewhere"
        linhas = [cab]
        por_arquivo: Dict[str, List[Erro]] = {}
        for e in meus:
            por_arquivo.setdefault(e.arquivo or "(project)", []).append(e)
        mostrados = 0
        for arq, erros in por_arquivo.items():
            if mostrados >= limite:
                break
            linhas.append(arq)
            for e in erros[: limite - mostrados]:
                linhas.append(_linha(e))
                mostrados += 1
        if len(meus) > mostrados:
            linhas.append(f"... and {len(meus) - mostrados} more error(s)")
        if r.lado == "backend" and any(_SINTAXE.search(e.mensagem) for e in r.erros):
            linhas.append("Note: javac stops at syntax errors; fixing them may reveal more errors.")
        if r.lado == "backend" and len(r.erros) >= 100:
            linhas.append("Note: javac reports at most 100 errors per run.")
        blocos.append("\n".join(linhas))
    return "\n\n".join(blocos)


# ------------------------------------------------------------- linha de comando
def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("codigo", type=Path, help="pasta com backend/ e frontend/")
    p.add_argument("--lado", choices=("backend", "frontend"), action="append",
                   help="só este lado (pode repetir); padrão: os dois")
    p.add_argument("--offline", action="store_true", help="mvn -o (sem baixar dependências)")
    p.add_argument("--modulos", default="", help="Users,Catalog,... para contar erros por módulo")
    p.add_argument("--json", type=Path, help="grava o resultado completo neste arquivo")
    p.add_argument("--timeout", type=int, default=TIMEOUT_S)
    a = p.parse_args(argv)

    resultados = compilar(a.codigo, a.lado or ("backend", "frontend"), a.offline, a.timeout)
    if not resultados:
        print(f"nada para compilar em {a.codigo} (faltam backend/ e frontend/)", file=sys.stderr)
        return 2
    print(para_agente(resultados, limite=10_000))

    modulos = [m.strip() for m in a.modulos.split(",") if m.strip()]
    if modulos:
        print("\nErros por módulo:")
        for mod, erros in sorted(por_modulo(resultados, modulos).items()):
            print(f"  {mod}: {len(erros)}")
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(
            [dict(asdict(r), falha_de_ambiente=r.falha_de_ambiente) for r in resultados],
            ensure_ascii=False, indent=2))
    return 0 if all(r.ok for r in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
