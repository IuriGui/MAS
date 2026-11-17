"""Criação dos agentes: system prompt (prompts.py) + schema da saída (schemas.py).

- Agentes de módulo recebem o PROJECT CONTEXT no system prompt e são
  reaproveitados enquanto o contexto for o mesmo.
- Com um AmbienteDocker (sandbox.py), os Implementadores viram deep agents que
  trabalham dentro do container em vez de devolver saída estruturada.
"""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

from mas.agentes import prompts
from mas.config import (MAX_RETRIES_API, MODEL, MOSTRAR_RACIOCINIO, SANDBOX,
                        SANDBOX_IMAGEM, SANDBOX_PORTAS, SANDBOX_SUBAGENTE, TIMEOUT_API)
from mas.schemas import (FinalSpecification, GeneratedFiles, ImplementationPlan,
                         ModuleAnalysis, ModuleArchitecture, ModuleDataModel,
                         ProjectContext)

# etapa do módulo -> (regras do system prompt, schema da saída)
AGENTES_DE_MODULO = {
    "requisitos": (prompts.REQUISITOS, ModuleAnalysis),
    "esclarecedor": (prompts.ESCLARECEDOR, FinalSpecification),
    "modelador_dados": (prompts.MODELADOR_DE_DADOS, ModuleDataModel),
    "arquiteto_software": (prompts.ARQUITETO_SOFTWARE, ModuleArchitecture),
    "planejador": (prompts.PLANEJADOR, ImplementationPlan),
    "implementador_backend": (prompts.IMPLEMENTADOR_BACKEND, GeneratedFiles),
    "implementador_frontend": (prompts.IMPLEMENTADOR_FRONTEND, GeneratedFiles),
}
IMPLEMENTADORES = ("implementador_backend", "implementador_frontend")


def _modelo(**extra):
    return init_chat_model(MODEL, max_retries=MAX_RETRIES_API, timeout=TIMEOUT_API, **extra)


def _eh_gemini() -> bool:
    provedor = MODEL.split(":", 1)[0] if ":" in MODEL else ""
    return provedor in ("google_genai", "google_vertexai") or (
        not provedor and MODEL.startswith("gemini"))


def _opcoes_sandbox() -> dict:
    """Parâmetros do modelo dos Implementadores no sandbox: resumos do
    raciocínio no histórico (só Gemini; outros provedores não aceitam o nome)."""
    return {"include_thoughts": True} if MOSTRAR_RACIOCINIO and _eh_gemini() else {}


def criar_agente_estruturado(system_prompt: str, response_format):
    """Agente sem tools que devolve um objeto Pydantic em `structured_response`."""
    return create_agent(
        model=_modelo(),
        system_prompt=system_prompt,
        response_format=response_format,
    )


@lru_cache(maxsize=1)
def criar_arquiteto_chefe():
    """O Arquiteto-chefe não recebe PROJECT CONTEXT: é ele que o produz."""
    return criar_agente_estruturado(prompts.ARQUITETO_CHEFE, ProjectContext)


@lru_cache(maxsize=16)
def _agente_cache(regras: str, response_format, contexto_json: str, enxuto: bool = False):
    contexto = ProjectContext.model_validate_json(contexto_json)
    return criar_agente_estruturado(prompts.com_contexto(contexto, regras, enxuto),
                                    response_format)


def criar_agente_de_modulo(regras: str, response_format, contexto: ProjectContext,
                           enxuto: bool = False):
    """Agente de módulo: regras + PROJECT CONTEXT do projeto no system prompt
    (`enxuto`: só as partes que afetam o código, para os Implementadores).

    Reaproveita o agente enquanto o contexto for o mesmo.
    """
    return _agente_cache(regras, response_format, contexto.model_dump_json(), enxuto)


def criar_agentes(contexto: ProjectContext, ambiente=None) -> dict:
    """Um agente por etapa do módulo. Com `ambiente` (AmbienteDocker), os
    Implementadores trabalham no sandbox dele."""
    agentes = {etapa: criar_agente_de_modulo(regras, schema, contexto)
               for etapa, (regras, schema) in AGENTES_DE_MODULO.items()
               if etapa not in IMPLEMENTADORES}
    return {**agentes, **criar_implementadores(contexto, ambiente)}


def criar_implementadores(contexto: ProjectContext, ambiente=None) -> dict:
    if ambiente is None:
        return {e: criar_agente_de_modulo(*AGENTES_DE_MODULO[e], contexto, enxuto=True)
                for e in IMPLEMENTADORES}
    return {e: criar_agente_sandbox(prompts.regras_sandbox(AGENTES_DE_MODULO[e][0]),
                                    contexto, ambiente.sandbox, ambiente.descricao())
            for e in IMPLEMENTADORES}


# ------------------------------------------------------------ modo sandbox
class AgenteSandbox:
    """Implementador que trabalha dentro do DockerSandbox (deep agent).

    Escreve os arquivos do lote direto no workspace do container e usa `execute`
    para compilar e testar. Não tem saída estruturada: o pipeline colhe os
    arquivos do disco (codigo/coleta.py) e valida como antes.
    """

    def __init__(self, grafo, sandbox, ambiente: str = ""):
        self.grafo = grafo
        self.sandbox = sandbox        # precisa de host_dir e caminho_no_container()
        self.ambiente = ambiente      # descrição dos serviços da rede (banco, cache)

    def invoke(self, entrada, config=None):
        """Roda o grafo guardando o último estado: se o limite de passos estourar,
        devolve as mensagens até ali (com `limite_passos`), para que os tokens
        gastos entrem nas métricas e o que foi gravado seja colhido."""
        from langgraph.errors import GraphRecursionError

        if not hasattr(self.grafo, "stream"):        # grafos falsos dos testes
            return self.grafo.invoke(entrada, config=config)
        ultimo = {"messages": []}
        try:
            for estado in self.grafo.stream(entrada, config=config, stream_mode="values"):
                ultimo = estado
        except GraphRecursionError:
            return {**ultimo, "limite_passos": True}
        return ultimo


def _provedor(modelo) -> str:
    """Provedor do modelo, como o deepagents o usa para escolher o HarnessProfile."""
    try:
        from deepagents._models import get_model_provider
        provedor = get_model_provider(modelo)
        if provedor:
            return provedor
    except ImportError:
        pass
    return MODEL.split(":", 1)[0] if ":" in MODEL else ""


def desligar_subagente(modelo) -> bool:
    """Tira o subagente general-purpose (ferramenta `task`) que o
    create_deep_agent adiciona sozinho.

    Ele nunca apareceu nos lotes medidos, a descrição da ferramenta (~2 mil
    caracteres) ia em toda chamada ao modelo, e as chamadas dele não ficam no
    estado do agente pai (relato 10). O deepagents só permite desligá-lo pelo
    HarnessProfile do provedor; o registro vale para o processo inteiro e é
    idempotente (registrar de novo mescla com o que já existe).
    """
    from deepagents import (GeneralPurposeSubagentProfile, HarnessProfile,
                            register_harness_profile)
    provedor = _provedor(modelo)
    if not provedor:
        print("  AVISO: provedor do modelo desconhecido; subagente `task` continua ligado")
        return False
    register_harness_profile(provedor, HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))
    return True


def criar_agente_sandbox(regras: str, contexto: ProjectContext, sandbox,
                         ambiente: str = "") -> AgenteSandbox:
    from deepagents import create_deep_agent

    modelo = _modelo(**_opcoes_sandbox())
    if not SANDBOX_SUBAGENTE:
        desligar_subagente(modelo)
    grafo = create_deep_agent(
        model=modelo,
        system_prompt=prompts.com_contexto(contexto, regras, enxuto=True),
        backend=sandbox,
    )
    return AgenteSandbox(grafo, sandbox, ambiente)


@contextmanager
def ambiente_sandbox(codigo_dir: Path, contexto: ProjectContext):
    """AmbienteDocker (rede + banco/cache do tech_stack + sandbox) montando
    `codigo_dir`, ou None com SANDBOX=0. Destruído na saída, mesmo com erro."""
    if not SANDBOX:
        yield None
        return
    from mas.agentes.sandbox import AmbienteDocker, servicos_do_contexto
    amb = AmbienteDocker(str(codigo_dir), servicos_do_contexto(contexto),
                         imagem=SANDBOX_IMAGEM, portas=SANDBOX_PORTAS)
    print(f"[sandbox] subindo rede {amb.rede} "
          f"({', '.join(s.nome for s in amb.servicos) or 'sem serviços'})")
    with amb:
        print(amb.descricao())
        yield amb
