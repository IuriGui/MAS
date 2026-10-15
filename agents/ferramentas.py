"""Autonomia dos agentes (F2): tools de percepção do ambiente e submissão validada.

- **Sessão:** o orquestrador abre uma sessão por invocação (`sessao(...)`) com a
  leitura do ambiente, o módulo, o lado do código e a validação da etapa. As
  tools e a submissão consultam a sessão aberta (contextvar), então o mesmo
  agente serve a qualquer módulo.
- **Tools de percepção** (só leitura, sobre `ambiente.Leitura`): o agente decide o
  que precisa consultar. Nomes, descrições e respostas em inglês, como os prompts.
- **Submissão validada:** a tool de saída estruturada valida a resposta em
  código (`validation.py`) no momento da submissão. Se reprovada, o agente recebe
  os erros como resultado da tool e corrige dentro do próprio loop, sem perder o
  que já leu. A validação do orquestrador continua como juiz final.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, model_validator

from ambiente import Leitura

# nome em inglês (vocabulário dos agentes) -> chave do artefato no ambiente
ARTEFATOS_EN = {
    "analysis": "analise",
    "specification": "especificacao",
    "data_model": "modelo_dados",
    "architecture": "arquitetura",
    "plan": "plano",
}


@dataclass
class Sessao:
    leitura: Optional[Leitura] = None
    modulo: str = ""
    lado: Optional[str] = None
    validar: Optional[Callable[[BaseModel], List[str]]] = None
    reprovacoes: List[List[str]] = field(default_factory=list)
    ultima_reprovada: Optional[BaseModel] = None  # para diagnóstico (.invalido.json)


_SESSAO: ContextVar[Optional[Sessao]] = ContextVar("sessao_agente", default=None)


@contextmanager
def sessao(s: Sessao) -> Iterator[Sessao]:
    token = _SESSAO.set(s)
    try:
        yield s
    finally:
        _SESSAO.reset(token)


def sessao_atual() -> Optional[Sessao]:
    return _SESSAO.get()


# ------------------------------------------------------------- submissão
class SubmissaoReprovada(Exception):
    """Não é ValueError de propósito: o Pydantic deixa passar sem embrulhar."""

    def __init__(self, erros: List[str]):
        super().__init__("; ".join(erros))
        self.erros = erros


def com_validacao(schema: type) -> type:
    """Subclasse do schema que valida a submissão na sessão aberta.

    Mantém o nome do schema, que é o nome da tool de saída para o modelo.
    """
    class Submissao(schema):
        @model_validator(mode="after")
        def _validar_na_sessao(self):
            s = sessao_atual()
            if s and s.validar:
                erros = s.validar(self)
                if erros:
                    s.reprovacoes.append(list(erros))
                    s.ultima_reprovada = para_base(self)
                    raise SubmissaoReprovada(erros)
            return self

    Submissao.__name__ = schema.__name__
    Submissao.__qualname__ = schema.__qualname__
    Submissao.__doc__ = schema.__doc__
    Submissao.esquema_base = schema
    return Submissao


def para_base(obj):
    """Converte a submissão aprovada para o schema original."""
    base = getattr(type(obj), "esquema_base", None)
    return base.model_validate(obj.model_dump()) if base else obj


def _causa(exc: BaseException) -> BaseException:
    """A SubmissaoReprovada original, se estiver na cadeia de causas (o LangChain
    embrulha a exceção do parse); senão, a exceção mais interna."""
    atual, vistas = exc, set()
    while atual is not None and id(atual) not in vistas:
        if isinstance(atual, SubmissaoReprovada):
            return atual
        vistas.add(id(atual))
        proxima = getattr(atual, "source", None) or atual.__cause__ or atual.__context__
        if proxima is None:
            return atual
        atual = proxima
    return exc


def mensagem_reprovacao(exc: Exception) -> str:
    fonte = _causa(exc)
    if isinstance(fonte, SubmissaoReprovada):
        return ("Your submission was REJECTED by the automatic validation. Fix ALL "
                "points below and submit again:\n- " + "\n- ".join(fonte.erros))
    return (f"Your submission does not match the required schema: {fonte}. "
            "Fix it and submit again.")


# ------------------------------------------------------------- tools
def _leitura() -> Optional[Leitura]:
    s = sessao_atual()
    return s.leitura if s else None


_SEM_AMBIENTE = "The shared environment is not available in this run."


def _lado(side: str) -> str:
    s = sessao_atual()
    return side or (s.lado if s and s.lado else "backend")


@tool
def get_artifact(module: str, artifact: str) -> str:
    """Read an artifact that a module already published in the shared environment
    (it passed validation). `artifact` is one of: analysis, specification,
    data_model, architecture, plan. Use it for this module or for other modules,
    e.g. to check the data model or the API of a module you depend on."""
    ler = _leitura()
    if not ler:
        return _SEM_AMBIENTE
    chave = ARTEFATOS_EN.get(artifact.strip().lower())
    if not chave:
        return f"Unknown artifact '{artifact}'. Use one of: {list(ARTEFATOS_EN)}."
    return ler.artefato(module, chave)


@tool
def get_contracts() -> str:
    """List what the OTHER modules already published for others to consume:
    public services (interface, method, parameters, return type), domain events
    and endpoints. Use the exact names found here instead of inventing them."""
    ler = _leitura()
    if not ler:
        return _SEM_AMBIENTE
    s = sessao_atual()
    return ler.contratos(exceto=s.modulo if s else None)


@tool
def list_files(prefix: str = "", side: str = "") -> str:
    """List the code files that already exist in the project (base and modules),
    optionally under a path prefix. `side` is backend or frontend (default: the
    side you are working on)."""
    ler = _leitura()
    return ler.listar_arquivos(_lado(side), prefix) if ler else _SEM_AMBIENTE


@tool
def read_file(path: str, side: str = "") -> str:
    """Read the full content of an existing code file. `side` is backend or
    frontend (default: the side you are working on)."""
    ler = _leitura()
    return ler.ler_arquivo(_lado(side), path) if ler else _SEM_AMBIENTE


@tool
def get_signatures(path: str, side: str = "") -> str:
    """Read only the public signatures (types, constructors, methods, fields,
    exports) of an existing code file. Cheaper than read_file when you only need
    to know how to call it."""
    ler = _leitura()
    return ler.assinaturas(_lado(side), path) if ler else _SEM_AMBIENTE


# tools por papel
ESPECIFICACAO = (get_artifact, get_contracts)
PLANEJAMENTO = (get_artifact, get_contracts, list_files, get_signatures)
IMPLEMENTACAO = (get_artifact, get_contracts, list_files, read_file, get_signatures)
POR_NOME = {t.name: t for t in IMPLEMENTACAO}


def instrucoes(tools, nome_saida: str, limite: int) -> str:
    """Parágrafo do system prompt sobre autonomia (anexado às regras do papel)."""
    partes = ["\nAUTONOMY (tools and submission):"]
    if tools:
        partes.append(
            "- You work inside a shared environment that holds what the other agents "
            "already produced and the project accepted. Use the read-only tools "
            f"({', '.join(t.name for t in tools)}) to look up whatever you need that "
            "is not in the message. Do not guess names, signatures or files: read them.")
    partes.append(
        f"- You have a budget of about {limite} steps; each tool call and each "
        "submission uses one. Read only what you need.")
    partes.append(
        f"- You finish by calling the output tool {nome_saida} with your COMPLETE "
        "answer. It is validated automatically; if it is rejected you receive the "
        "list of problems: fix ALL of them and submit again.")
    return "\n".join(partes) + "\n"
