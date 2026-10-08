"""Onde os arquivos gerados podem morar.

Regras fixas (independentes do modelo) que o Planejador, os Implementadores e
os validadores compartilham:
- backend JVM: src/main/<java|kotlin>/<pacote do módulo>/ e src/test/...;
  outras linguagens: src/<modulo>/ e tests/<modulo>/;
- frontend: src/modules/<modulo>/, com um arquivo de rotas
  src/modules/<modulo>/routes.<ext> que a base importa;
- migrations: src/main/resources/db/migration/V<nnn>__<modulo>.sql, geradas
  em código a partir do modelo de dados (a base só pode usar V0__*.sql).
"""
import posixpath
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from schemas import ProjectContext

MIGRACOES_DIR = "src/main/resources/db/migration"
PREFIXO_MIGRACAO_BASE = f"{MIGRACOES_DIR}/V0__"
FRONTEND_MODULOS = "src/modules"
ENTRADA_ROTAS = "routes"
EXTENSOES_FRONTEND = (".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte", ".mjs")
_FQCN = re.compile(r"^src/(?:main|test)/(?:java|kotlin)/(.+)\.(?:java|kt)$")


def slugify(texto: str) -> str:
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "modulo"


def tem_frontend(ctx: ProjectContext) -> bool:
    nome = ctx.tech_stack.frontend.name.strip().lower()
    return nome not in ("", "none", "n/a", "na", "nenhum")


def linguagem_jvm(ctx: ProjectContext) -> Optional[str]:
    nome = ctx.tech_stack.language.name.strip().lower()
    if "kotlin" in nome:
        return "kotlin"
    if "java" in nome:
        return "java"
    return None


@dataclass(frozen=True)
class Layout:
    backend_prefixos: Tuple[str, ...]
    frontend_prefixo: str
    rotas: str  # caminho do arquivo de rotas, sem extensão


def layout_modulo(ctx: ProjectContext, nome: str, java_package: str) -> Layout:
    s = slugify(nome)
    jvm = linguagem_jvm(ctx)
    if jvm:
        pkg = java_package.strip().strip(".").replace(".", "/")
        backend = (f"src/main/{jvm}/{pkg}/", f"src/test/{jvm}/{pkg}/")
    else:
        backend = (f"src/{s}/", f"tests/{s}/")
    front = f"{FRONTEND_MODULOS}/{s}/"
    return Layout(backend, front, front + ENTRADA_ROTAS)


def rotas_dos_modulos(nomes: Iterable[str]) -> List[str]:
    return [f"{FRONTEND_MODULOS}/{slugify(n)}/{ENTRADA_ROTAS}" for n in nomes]


def prefixos_proibidos_base(ctx: ProjectContext, pacote_base: str,
                            nomes: Iterable[str]) -> List[str]:
    """Pastas que pertencem aos módulos: a base não pode gerar nada nelas."""
    jvm = linguagem_jvm(ctx)
    base = pacote_base.strip(".").replace(".", "/")
    prefixos = [f"{FRONTEND_MODULOS}/"]
    for n in nomes:
        s = slugify(n)
        for variante in {s, s.replace("_", "")}:
            if jvm:
                prefixos += [f"src/main/{jvm}/{base}/{variante}/",
                             f"src/test/{jvm}/{base}/{variante}/"]
            else:
                prefixos += [f"src/{variante}/", f"tests/{variante}/"]
    return prefixos


def migracao_modulo(nome: str, numero: int) -> str:
    return f"{MIGRACOES_DIR}/V{numero:03d}__{slugify(nome)}.sql"


def fqcn(caminho: str) -> Optional[str]:
    """src/main/java/a/b/C.java -> a.b.C"""
    m = _FQCN.match(caminho)
    return m.group(1).replace("/", ".") if m else None


def sem_extensao(caminho: str) -> str:
    raiz, ext = posixpath.splitext(caminho)
    return raiz if ext else caminho


def caminho_invalido(caminho: str) -> Optional[str]:
    if not caminho or not caminho.strip():
        return "caminho vazio"
    if caminho.startswith("/") or "\\" in caminho or re.match(r"^[A-Za-z]:", caminho):
        return "use caminho relativo com '/'"
    if ".." in caminho.split("/") or posixpath.normpath(caminho) != caminho:
        return "caminho não normalizado (sem '..', './' ou '//')"
    return None


def limpar_conteudo(conteudo: str) -> str:
    """Remove cercas de markdown que modelos às vezes colocam em volta do código."""
    texto = conteudo.strip("\n")
    linhas = texto.split("\n")
    if linhas and linhas[0].strip().startswith("```"):
        linhas = linhas[1:]
        if linhas and linhas[-1].strip().startswith("```"):
            linhas = linhas[:-1]
    return "\n".join(linhas).rstrip() + "\n"
