from functools import lru_cache
from typing import Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model

from agents import ferramentas
from config import (AUTONOMIA, LIMITE_CHAMADAS_AGENTE, MAX_RETRIES_API, MODEL,
                    TIMEOUT_API)
from schemas import ProjectContext
from system_prompts.system_prompts import com_contexto


def _modelo():
    return init_chat_model(MODEL, max_retries=MAX_RETRIES_API, timeout=TIMEOUT_API)


def criar_agente_estruturado(system_prompt: str, response_format, tools: Sequence = (),
                             modelo=None, autonomia: bool = AUTONOMIA):
    """Agente que devolve um objeto Pydantic em `structured_response`.

    Com autonomia (F2): recebe as tools de leitura do ambiente, a submissão é
    validada dentro do loop (ToolStrategy + ferramentas.com_validacao) e cada
    invocação tem no máximo LIMITE_CHAMADAS_AGENTE chamadas ao modelo.
    Sem autonomia: agente sem tools da linha de base.
    """
    modelo = modelo or _modelo()
    if not autonomia:
        return create_agent(model=modelo, system_prompt=system_prompt,
                            response_format=response_format)
    agente = create_agent(
        model=modelo,
        tools=list(tools),
        system_prompt=system_prompt + ferramentas.instrucoes(
            tools, response_format.__name__, LIMITE_CHAMADAS_AGENTE),
        response_format=ToolStrategy(ferramentas.com_validacao(response_format),
                                     handle_errors=ferramentas.mensagem_reprovacao),
        middleware=[
            ferramentas.EntregaNoFimDoOrcamento(LIMITE_CHAMADAS_AGENTE,
                                                response_format.__name__),
            ModelCallLimitMiddleware(run_limit=LIMITE_CHAMADAS_AGENTE, exit_behavior="end"),
        ],
    )
    # Passos do grafo por invocação: quem limita é o orçamento de chamadas ao
    # modelo (middleware acima); este teto só protege contra um laço do grafo.
    agente.limite_passos = 4 * LIMITE_CHAMADAS_AGENTE + 10
    return agente


@lru_cache(maxsize=16)
def _agente_cache(regras: str, response_format, contexto_json: str, nomes_tools: tuple):
    contexto = ProjectContext.model_validate_json(contexto_json)
    tools = [ferramentas.POR_NOME[n] for n in nomes_tools]
    return criar_agente_estruturado(com_contexto(contexto, regras), response_format, tools)


def criar_agente_de_modulo(regras: str, response_format, contexto: ProjectContext,
                           tools: Sequence = ()):
    """Agente de módulo: regras + PROJECT CONTEXT do projeto no system prompt.

    Reaproveita o agente enquanto o contexto for o mesmo.
    """
    return _agente_cache(regras, response_format, contexto.model_dump_json(),
                         tuple(t.name for t in tools))  # tools não são hashable
