from functools import lru_cache

from agents._base import criar_agente_estruturado
from schemas import ProjectContext
from system_prompts.system_prompts import ARQUITETO_CHEFE


@lru_cache(maxsize=1)
def get_agente():
    return criar_agente_estruturado(ARQUITETO_CHEFE, ProjectContext)
