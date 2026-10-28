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
- **Comunicação (F3):** `ask_agent` pergunta ao dono de um artefato anterior do
  módulo e recebe a resposta na hora; `report_blocker` pede a revisão desse
  artefato. As mensagens passam pelo `Canal` que o orquestrador abre, que as
  registra e decide o que fazer com os bloqueios.
- **Build:** na correção dos erros de build, `request_change` pede ao dono de um
  arquivo de outro módulo (ou da base) que o altere; o dono atende ou recusa na
  hora, e a resposta volta como resultado da tool.
"""
import functools
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional, Tuple

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, model_validator

from ambiente import Leitura
from config import COMUNICACAO, LIMITE_LEITURA, MAX_CONSULTAS_AGENTE

# nome em inglês (vocabulário dos agentes) -> chave do artefato no ambiente
ARTEFATOS_EN = {
    "analysis": "analise",
    "specification": "especificacao",
    "data_model": "modelo_dados",
    "architecture": "arquitetura",
    "plan": "plano",
}


# etapa -> papel em inglês (como os agentes se chamam entre si)
PAPEIS = {
    "requisitos": "requirements_analyst",
    "esclarecedor": "clarifier",
    "modelador_dados": "data_modeler",
    "arquiteto_software": "software_architect",
    "planejador": "planner",
    "implementador_backend": "backend_developer",
    "implementador_frontend": "frontend_developer",
}
ETAPA_DO_PAPEL = {v: k for k, v in PAPEIS.items()}

# A quem cada etapa pode perguntar: donos dos artefatos que ela consome.
CONSULTA = {
    "esclarecedor": ("requisitos",),
    "modelador_dados": ("esclarecedor",),
    "arquiteto_software": ("esclarecedor", "modelador_dados"),
    "planejador": ("esclarecedor", "modelador_dados", "arquiteto_software"),
    "implementador_backend": ("esclarecedor", "modelador_dados", "arquiteto_software",
                              "planejador"),
    "implementador_frontend": ("esclarecedor", "arquiteto_software", "planejador"),
}
# Quem pode pedir revisão (bloqueio). Os Implementadores só perguntam: reabrir
# etapas no meio dos lotes de código fica fora desta fase.
BLOQUEIO = {e: d for e, d in CONSULTA.items() if not e.startswith("implementador")}


@dataclass
class Canal:
    """Canal de comunicação de uma invocação, aberto pelo orquestrador."""
    etapa: str
    consultar: Optional[Callable[[str, str], str]] = None  # (etapa destino, pergunta) -> resposta
    bloquear: Optional[Callable[[str, str], None]] = None  # (etapa destino, problema): registra
    max_consultas: int = 3
    bloqueios: List[Tuple[str, str]] = field(default_factory=list)
    # build: (caminho, problema) -> resposta do dono do arquivo
    pedir_mudanca: Optional[Callable[[str, str], str]] = None
    pedidos: int = 0

    @property
    def pode_consultar(self) -> Tuple[str, ...]:
        return CONSULTA.get(self.etapa, ())

    @property
    def pode_bloquear(self) -> Tuple[str, ...]:
        return BLOQUEIO.get(self.etapa, ())


@dataclass
class Sessao:
    etapa: str = ""
    leitura: Optional[Leitura] = None
    modulo: str = ""
    lado: Optional[str] = None
    validar: Optional[Callable[[BaseModel], List[str]]] = None
    reprovacoes: List[List[str]] = field(default_factory=list)
    ultima_reprovada: Optional[BaseModel] = None  # para diagnóstico (.invalido.json)
    canal: Optional[Canal] = None
    consultas: int = 0


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


# ------------------------------------------------------------- observação
def _resumo_args(args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:60]!r}" for k, v in args.items())


def anotar(acao: str, args: dict, resultado: str) -> None:
    """Mostra no console e grava em acessos.jsonl uma ação do agente da sessão."""
    s = sessao_atual()
    if not s:
        return
    print(f"    · {s.etapa or '?'} → {acao}({_resumo_args(args)}) "
          f"[{len(resultado)} caracteres]")
    if s.leitura:
        s.leitura.anotar(s.etapa, s.modulo, acao, args, resultado)


def _observado(fn):
    """Anota cada chamada da tool (nome, argumentos, tamanho da resposta)."""
    assinatura = inspect.signature(fn)

    @functools.wraps(fn)
    def envolvida(*args, **kwargs):
        resultado = fn(*args, **kwargs)
        ligados = assinatura.bind(*args, **kwargs)
        ligados.apply_defaults()
        anotar(fn.__name__, dict(ligados.arguments), str(resultado))
        return resultado
    return envolvida


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
                    anotar(f"submit:{schema.__name__}", {}, "REJECTED: " + "; ".join(erros))
                    raise SubmissaoReprovada(erros)
                anotar(f"submit:{schema.__name__}", {}, "approved")
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
@_observado
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
@_observado
def get_contracts(module: str = "") -> str:
    """What the OTHER modules already published for others to consume.
    Without `module`: one line per public service (short type names), event
    names and endpoints of every module. With `module`: that module's full
    contract (fully qualified names, purpose, event payloads and triggers).
    The message already lists the other modules' public services and events:
    call this for their endpoints or for the details of one module, not to
    repeat what you already have. Use the exact names found here."""
    ler = _leitura()
    if not ler:
        return _SEM_AMBIENTE
    s = sessao_atual()
    return ler.contratos(exceto=s.modulo if s else None, modulo=module.strip())


@tool
@_observado
def list_files(prefix: str = "", side: str = "") -> str:
    """List the code files that already exist in the project (base and modules),
    optionally under a path prefix. `side` is backend or frontend (default: the
    side you are working on)."""
    ler = _leitura()
    return ler.listar_arquivos(_lado(side), prefix) if ler else _SEM_AMBIENTE


@tool
@_observado
def read_files(paths: List[str], side: str = "") -> str:
    """Read the full content of SEVERAL existing code files in one call. Pass every
    file you need at once: each call costs a step of your budget and makes the
    conversation longer. `side` is backend or frontend (default: the side you are
    working on). Above a size limit, the remaining files come with their public
    signatures only."""
    ler = _leitura()
    return ler.ler_arquivos(_lado(side), paths, LIMITE_LEITURA) if ler else _SEM_AMBIENTE


@tool
@_observado
def get_signatures(paths: List[str], side: str = "") -> str:
    """Read only the public signatures (types, constructors, methods, fields,
    exports) of SEVERAL existing code files in one call. Cheaper than read_files
    when you only need to know how to call them. Pass every file at once."""
    ler = _leitura()
    return ler.assinaturas_de(_lado(side), paths) if ler else _SEM_AMBIENTE


_SEM_CANAL = "Communication with other agents is not available in this run."


def _destino(recipient: str, permitidos: Tuple[str, ...]):
    """(etapa, None) se o destinatário é permitido; senão (None, mensagem de erro)."""
    etapa = ETAPA_DO_PAPEL.get(recipient.strip().lower())
    if etapa not in permitidos:
        return None, (f"You cannot address '{recipient}'. Allowed: "
                      f"{[PAPEIS[e] for e in permitidos]}.")
    return etapa, None


@tool
@_observado
def ask_agent(recipient: str, question: str) -> str:
    """Ask a question to the author of an earlier artifact of THIS module and get
    the answer right away. Who owns what:
      - requirements_analyst: the original analysis and the problems it found;
      - clarifier: requirements and business rules (the final specification);
      - data_modeler: tables, columns, constraints and relationships;
      - software_architect: endpoints, public services, events and dependencies;
      - planner: the file plan (which file does what and what it exports).
    Ask instead of assuming whenever the artifacts do not state something
    explicitly. Examples: "Can a deactivated customer still receive deliveries of
    approved orders?", "Is sku unique per tenant or globally?", "Which status code
    should a duplicate e-mail return?". Ask one precise question at a time and
    include the IDs (FR, table, endpoint) it is about. The recipient answers from
    its artifact and does not change it. The number of questions per run is
    limited."""
    s = sessao_atual()
    c = s.canal if s else None
    if not c:
        return _SEM_CANAL
    etapa, erro = _destino(recipient, c.pode_consultar)
    if erro:
        return erro
    if s.consultas >= c.max_consultas:
        return "Question budget exhausted for this run: decide with what you have."
    s.consultas += 1
    return c.consultar(etapa, question)


@tool
@_observado
def report_blocker(recipient: str, problem: str) -> str:
    """Report that an earlier artifact of THIS module has a defect that prevents you
    from doing your job correctly and that you must not work around within your own
    rules. Examples: a column or table you need is missing from the closed data
    model; two requirements contradict each other; an endpoint the screens need
    does not exist in the architecture; an answer from ask_agent admitted a gap.
    Not a blocker: a design you would have done differently, or a detail you can
    decide within your own role. Be specific: what is wrong, where (IDs), and what
    you need. The recipient will be asked to revise its artifact, and you will run
    again with the revised version. Even after reporting, finish by submitting
    your best answer with the current inputs."""
    s = sessao_atual()
    c = s.canal if s else None
    if not c:
        return _SEM_CANAL
    etapa, erro = _destino(recipient, c.pode_bloquear)
    if erro:
        return erro
    if (etapa, problem) not in c.bloqueios:
        c.bloqueios.append((etapa, problem))
        c.bloquear(etapa, problem)
    return (f"Blocker sent to the {recipient}. It will be asked to revise its artifact "
            "and you will run again with the revised version. Now submit your best "
            "answer with the current inputs.")


@tool
@_observado
def request_change(path: str, problem: str) -> str:
    """Ask the owner of a file that is NOT yours (another module, or the shared
    base) to change it, when a build error in your files can only be fixed there.
    Examples: a dependency missing from pom.xml or package.json; a method, field or
    export your code needs that the other module does not offer; a base type with a
    wrong signature. `path` is the file to change (same side as yours); `problem`
    says what is wrong and exactly what you need (names, signatures, the build
    error). The owner answers right away: it either changes the file or declines
    and explains (e.g. how you should call it instead). First check with
    get_signatures/read_files whether you can fix it in your own files: adapting
    your call is usually right. The number of requests is limited."""
    s = sessao_atual()
    c = s.canal if s else None
    if not c or not c.pedir_mudanca:
        return _SEM_CANAL
    c.pedidos += 1
    return c.pedir_mudanca(path, problem)


# tools por papel
ESPECIFICACAO = (get_artifact, get_contracts)
PLANEJAMENTO = (get_artifact, get_contracts, list_files, get_signatures)
IMPLEMENTACAO = (get_artifact, get_contracts, list_files, read_files, get_signatures)
CORRECAO = (list_files, read_files, get_signatures, get_contracts)
POR_NOME = {t.name: t for t in IMPLEMENTACAO + (ask_agent, report_blocker, request_change)}
_LEITURA_POR_PAPEL = {
    "requisitos": ESPECIFICACAO, "esclarecedor": ESPECIFICACAO,
    "modelador_dados": ESPECIFICACAO, "arquiteto_software": ESPECIFICACAO,
    "planejador": PLANEJAMENTO,
    "implementador_backend": IMPLEMENTACAO, "implementador_frontend": IMPLEMENTACAO,
}


def para(etapa: str, comunicacao: bool = COMUNICACAO) -> tuple:
    """Tools do agente de uma etapa: leitura do ambiente e, com comunicação,
    ask_agent / report_blocker conforme a quem ele pode se dirigir."""
    tools = _LEITURA_POR_PAPEL[etapa]
    if comunicacao and CONSULTA.get(etapa):
        tools += (ask_agent,)
    if comunicacao and BLOQUEIO.get(etapa):
        tools += (report_blocker,)
    return tools


# ------------------------------------------------------------- orçamento
AVISO_ORCAMENTO = (
    "\n\nBUDGET EXHAUSTED: you cannot read anything else. Submit your COMPLETE "
    "answer NOW with the output tool {saida}, using what you already have.")


class EntregaNoFimDoOrcamento(AgentMiddleware):
    """Nas últimas `reserva` chamadas do orçamento, o agente só pode submeter.

    Sem isso, um agente pode gastar o orçamento inteiro lendo o ambiente e nunca
    entregar (aconteceu no Implementador frontend da base). Aqui, as tools de
    leitura e comunicação saem da chamada e o system prompt avisa; como o
    ToolStrategy obriga a chamar alguma tool, só sobra a de saída. A reserva de 2
    deixa uma chance de corrigir uma submissão reprovada.
    """

    def __init__(self, limite: int, nome_saida: str, reserva: int = 2):
        super().__init__()
        self.limite, self.nome_saida, self.reserva = limite, nome_saida, reserva

    def wrap_model_call(self, request, handler):
        feitas = sum(1 for m in request.messages if getattr(m, "type", "") == "ai")
        if feitas >= self.limite - self.reserva:
            texto = request.system_message.content if request.system_message else ""
            request = request.override(
                tools=[],
                system_message=SystemMessage(
                    content=texto + AVISO_ORCAMENTO.format(saida=self.nome_saida)))
        return handler(request)


_COMUNICACAO = {"ask_agent", "report_blocker", "request_change"}

DONOS = {
    "requisitos": "the original analysis and its problems",
    "esclarecedor": "requirements and business rules, in the final specification",
    "modelador_dados": "tables, columns, constraints and relationships",
    "arquiteto_software": "endpoints, public services, events and dependencies",
    "planejador": "the file plan (which file does what and what it exports)",
}

# Dicas por papel: as dúvidas típicas de cada etapa e a quem levá-las.
DICAS = {
    "esclarecedor": (
        "If resolving a problem depends on what the requirements_analyst meant or "
        "found, ask it before deciding."),
    "modelador_dados": (
        "Typical questions for the clarifier: whether a value is unique, optional or "
        "limited to a set of values; whether records are deleted, deactivated or kept "
        "as history; which module owns data shared with others. If two requirements "
        "you must model contradict each other, call report_blocker to the clarifier."),
    "arquiteto_software": (
        "The data model is closed: if an endpoint or rule needs data the model does "
        "not have, call report_blocker to the data_modeler instead of working around "
        "it. If the expected behavior of a requirement is unclear (who may do it, "
        "what happens on conflict, which status code), ask the clarifier."),
    "planejador": (
        "If an endpoint, service or screen cannot be mapped to the data model, or a "
        "screen needs data that no endpoint returns, call report_blocker to the "
        "software_architect. If you are unsure where a responsibility belongs, ask "
        "the software_architect."),
    "implementador_backend": (
        "Before inventing a business rule, validation, error, status transition or "
        "parameter value that the specification and the plan do not state, ask the "
        "clarifier (rules) or the software_architect (API behavior). If the plan and "
        "the architecture disagree about a file, ask the planner."),
    "implementador_frontend": (
        "Before inventing screen behavior, a field validation or which role sees "
        "which action, ask the clarifier (rules) or the software_architect (API "
        "behavior). If the plan and the architecture disagree about a file, ask the "
        "planner."),
}


def instrucoes(tools, nome_saida: str, limite: int, etapa: str = "") -> str:
    """Blocos do system prompt sobre autonomia e trabalho em equipe (anexados às
    regras do papel)."""
    partes = ["\nAUTONOMY (tools and submission):"]
    if tools:
        partes.append(
            "- You work inside a shared environment that holds what the other agents "
            "already produced and the project accepted. Use the read-only tools "
            f"({', '.join(t.name for t in tools if t.name not in _COMUNICACAO)}) to "
            "look up whatever you need that "
            "is not in the message. Do not guess names, signatures or files: read them.")
    nomes = {t.name for t in tools}
    if "read_files" in nomes or "get_signatures" in nomes:
        partes.append(
            "- Read files in batches: pass ALL the paths you need in ONE read_files "
            "(or get_signatures) call instead of one call per file.")
    partes.append(
        f"- You have a budget of about {limite} steps; each tool call and each "
        "submission uses one. Read only what you need.")
    partes.append(
        f"- You finish by calling the output tool {nome_saida} with your COMPLETE "
        "answer. It is validated automatically; if it is rejected you receive the "
        "list of problems: fix ALL of them and submit again.")
    if "ask_agent" in nomes:
        partes += _equipe(etapa, "report_blocker" in nomes)
    return "\n".join(partes) + "\n"


def _equipe(etapa: str, pode_bloquear: bool) -> List[str]:
    """Bloco TEAMWORK: com quem falar, quando perguntar e quando bloquear."""
    autores = [f"{PAPEIS[e]} ({DONOS[e]})" for e in CONSULTA.get(etapa, ()) if e in DONOS]
    partes = ["\nTEAMWORK (you are one agent of a team working on this module):"]
    if autores:
        partes.append("- The authors of the earlier artifacts are available: "
                      + "; ".join(autores) + ".")
    partes.append(
        "- ASK with ask_agent whenever you would otherwise have to assume something "
        "the artifacts do not state explicitly: the intent of a requirement, a "
        "business rule, an allowed value, a state transition, who may perform an "
        "action. A wrong assumption propagates to every later stage; a question costs "
        f"one step (up to {MAX_CONSULTAS_AGENTE} per run).")
    partes.append(
        "- Do NOT ask about what is already explicit in the message or readable with "
        "the tools.")
    if pode_bloquear:
        partes.append(
            "- If two artifacts contradict each other, or something you need is missing, "
            "do not silently work around it or invent it: call report_blocker to its "
            "author, then still submit your best answer.")
    if etapa in DICAS:
        partes.append("- " + DICAS[etapa])
    return partes
