"""Resumo das assinaturas públicas de um arquivo de código.

Usado quando o conteúdo inteiro não cabe no contexto de um lote e para mostrar
a API da base a cada módulo. Assim o Implementador sabe nomes de métodos,
construtores, campos e props sem receber os corpos.
"""
import posixpath
import re
from typing import List, Set

_JVM = (".java", ".kt")
_FRONT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue", ".svelte")
_DECL_TIPO = re.compile(r"\b(class|interface|enum|record|object)\s+\w+")
_PRIVADO = re.compile(r"^\s*private\b")
_TS_BLOCO = re.compile(r"^\s*(export\s+)?(default\s+)?(interface|type|enum)\s+\w+")


def _sem_corpo(linha: str) -> str:
    linha = linha.rstrip()
    return linha[:-1].rstrip() + " { ... }" if linha.endswith("{") else linha


_ANOTACAO = re.compile(r"^@(\w+)")
_CAMPO = re.compile(r"^(?:(?:private|protected|public)\s+)?(static\s+)?(final\s+)?"
                    r"([\w.]+(?:<[^=;]*>)?(?:\[\])*)\s+(\w+)\s*(=.*)?;$")
_TIPO_NOME = re.compile(r"\b(?:class|record|enum|interface)\s+(\w+)")


def _primeira_frase(javadoc: List[str]) -> str:
    """1ª frase do Javadoc (sem tags @param/@return), para dizer o que o método faz."""
    texto = " ".join(l.strip().lstrip("/*").strip() for l in javadoc)
    texto = texto.replace("*/", "").split(" @")[0].strip()
    frase = re.split(r"(?<=\.)\s", texto, maxsplit=1)[0].strip()
    return frase[:160] + ("…" if len(frase) > 160 else "")


def _lombok(classe: Set[str], campo: Set[str], tipo: str, nome: str,
            final: bool) -> List[str]:
    """Getters/setters que o Lombok gera para um campo (anotação na classe ou no campo)."""
    sufixo = nome[:1].upper() + nome[1:]
    saida = []
    if {"Getter", "Data", "Value"} & (classe | campo):
        prefixo = "is" if tipo == "boolean" else "get"
        saida.append(f"    public {tipo} {prefixo}{sufixo}() {{ ... }}  // Lombok")
    if not final and "Value" not in classe and {"Setter", "Data"} & (classe | campo):
        saida.append(f"    public void set{sufixo}({tipo} {nome}) {{ ... }}  // Lombok")
    return saida


def _jvm(conteudo: str) -> str:
    """Pacote, declaração de tipos e membros não privados (sem corpos).

    Cada método vem com a 1ª frase do Javadoc (o que ele faz). Getters, setters
    e construtores gerados pelo Lombok aparecem como se estivessem escritos.
    """
    saida, nivel = [], 0
    anot_classe: Set[str] = set()
    anot_membro: List[str] = []
    javadoc: List[str] = []
    no_javadoc = False
    tipo_topo = ""
    campos: List[tuple] = []          # (tipo, nome, final, inicializado), só de instância
    for linha in conteudo.splitlines():
        s = linha.strip()
        if no_javadoc or s.startswith("/**"):
            javadoc.append(s)
            no_javadoc = "*/" not in s
        elif s.startswith(("package ", "@")) and nivel == 0:
            if s.startswith("package "):
                saida.append(s)
            elif _ANOTACAO.match(s):
                anot_classe.add(_ANOTACAO.match(s).group(1))
        elif s and not s.startswith(("//", "/*", "*", "import ")):
            if nivel == 0 and _DECL_TIPO.search(s):
                saida.append(_sem_corpo(s))
                m = _TIPO_NOME.search(s)
                tipo_topo = m.group(1) if m else ""
                javadoc = []
            elif nivel == 1 and s.startswith("@"):
                anot_membro.append(s)       # vale para o membro seguinte
            elif nivel == 1 and not s.startswith("}"):
                # campo: sem parênteses antes do "=" (método/construtor têm)
                campo = _CAMPO.match(s) if "(" not in s.split("=")[0] else None
                nomes_anot = {_ANOTACAO.match(a).group(1) for a in anot_membro
                              if _ANOTACAO.match(a)}
                if campo and not campo.group(1):
                    tipo, nome, final = campo.group(3), campo.group(4), bool(campo.group(2))
                    campos.append((tipo, nome, final, bool(campo.group(5))))
                    if _PRIVADO.match(s):
                        saida += _lombok(anot_classe, nomes_anot, tipo, nome, final)
                    else:
                        saida.append("    " + s)
                elif not _PRIVADO.match(s) and (
                        "(" in s or s.endswith(";") or s.endswith(",") or _DECL_TIPO.search(s)):
                    # membros do tipo de topo: métodos, construtores, constantes
                    if "(" in s and javadoc:
                        frase = _primeira_frase(javadoc)
                        if frase:
                            saida.append(f"    // {frase}")
                    saida += ["    " + a for a in anot_membro]
                    saida.append("    " + _sem_corpo(s))
                anot_membro, javadoc = [], []
        nivel += linha.count("{") - linha.count("}")
        nivel = max(nivel, 0)

    # construtores e builder gerados pelo Lombok
    if tipo_topo:
        # @AllArgs: todos menos final já inicializado; @RequiredArgs: final sem valor
        todos = ", ".join(f"{t} {n}" for t, n, f, ini in campos if not (f and ini))
        finais = ", ".join(f"{t} {n}" for t, n, f, ini in campos if f and not ini)
        extras = []
        if "NoArgsConstructor" in anot_classe:
            extras.append(f"    public {tipo_topo}() {{ ... }}  // Lombok")
        if {"AllArgsConstructor", "Value"} & anot_classe:
            extras.append(f"    public {tipo_topo}({todos}) {{ ... }}  // Lombok")
        if {"RequiredArgsConstructor", "Data"} & anot_classe:
            extras.append(f"    public {tipo_topo}({finais}) {{ ... }}  // Lombok")
        if "Builder" in anot_classe:
            extras.append(f"    public static {tipo_topo}Builder builder() {{ ... }}  // Lombok")
        saida += extras
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
