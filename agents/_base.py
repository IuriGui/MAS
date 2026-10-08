from functools import lru_cache

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model

from config import MAX_RETRIES_API, MODEL, TIMEOUT_API
from schemas import ProjectContext
from system_prompts.system_prompts import com_contexto


def criar_agente_estruturado(system_prompt: str, response_format):
    """Agente sem tools que devolve um objeto Pydantic em `structured_response`."""
    return create_agent(
        model=init_chat_model(MODEL, max_retries=MAX_RETRIES_API, timeout=TIMEOUT_API),
        system_prompt=system_prompt,
        response_format=response_format,
    )


@lru_cache(maxsize=16)
def _agente_cache(regras: str, response_format, contexto_json: str):
    contexto = ProjectContext.model_validate_json(contexto_json)
    return criar_agente_estruturado(com_contexto(contexto, regras), response_format)


def criar_agente_de_modulo(regras: str, response_format, contexto: ProjectContext):
    """Agente de módulo: regras + PROJECT CONTEXT do projeto no system prompt.

    Reaproveita o agente enquanto o contexto for o mesmo.
    """
    return _agente_cache(regras, response_format, contexto.model_dump_json())
