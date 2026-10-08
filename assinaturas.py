"""Resumo das assinaturas públicas de um arquivo de código.

Usado quando o conteúdo inteiro não cabe no contexto de um lote e para mostrar
a API da base a cada módulo. Assim o Implementador sabe nomes de métodos,
construtores, campos e props sem receber os corpos.
"""
import posixpath
import re

_JVM = (".java", ".kt")
_FRONT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte")
_DECL_TIPO = re.compile(r"\b(class|interface|enum|record|object)\s+\w+")
_PRIVADO = re.compile(r"^\s*private\b")
_TS_BLOCO = re.compile(r"^\s*(export\s+)?(default\s+)?(interface|type|enum)\s+\w+")


def _sem_corpo(linha: str) -> str:
    linha = linha.rstrip()
    return linha[:-1].rstrip() + " { ... }" if linha.endswith("{") else linha


def _jvm(conteudo: str) -> str:
    """Pacote, declaração de tipos e membros não privados (sem corpos)."""
    saida, nivel = [], 0
    for linha in conteudo.splitlines():
        s = linha.strip()
        if s.startswith(("package ", "@")) and nivel == 0:
            if s.startswith("package "):
                saida.append(s)
        elif s and not s.startswith(("//", "/*", "*", "import ")):
            if nivel == 0 and _DECL_TIPO.search(s):
                saida.append(_sem_corpo(s))
            elif nivel == 1 and not _PRIVADO.match(s) and not s.startswith("}"):
                # membros do tipo de topo: métodos, construtores, campos, constantes
                if "(" in s or s.endswith(";") or s.endswith(",") or _DECL_TIPO.search(s):
                    saida.append("    " + _sem_corpo(s))
        nivel += linha.count("{") - linha.count("}")
        nivel = max(nivel, 0)
    return "\n".join(saida)


def _front(conteudo: str) -> str:
    """Blocos interface/type/enum inteiros e a 1ª linha de cada export."""
    saida, linhas, i = [], conteudo.splitlines(), 0
    while i < len(linhas):
        linha = linhas[i]
        if _TS_BLOCO.match(linha):
            nivel = linha.count("{") - linha.count("}")
            bloco = [linha]
            while nivel > 0 and i + 1 < len(linhas):
                i += 1
                bloco.append(linhas[i])
                nivel += linhas[i].count("{") - linhas[i].count("}")
            saida += bloco
        elif linha.lstrip().startswith("export "):
            saida.append(_sem_corpo(linha.split("=>")[0] + ("=> ..." if "=>" in linha else "")))
        i += 1
    return "\n".join(saida)


def assinaturas(caminho: str, conteudo: str, limite_texto: int = 1500) -> str:
    """Resumo público do arquivo. Arquivos de configuração pequenos vão inteiros."""
    if caminho.endswith(_JVM):
        return _jvm(conteudo)
    if caminho.endswith(_FRONT):
        return _front(conteudo)
    if len(conteudo) <= limite_texto:
        return conteudo.strip()
    ext = posixpath.splitext(caminho)[1] or "arquivo"
    return f"({ext} com {len(conteudo)} caracteres; conteúdo omitido)"
