from agents import ferramentas
from agents._base import criar_agente_de_modulo
from schemas import FinalSpecification, ProjectContext
from system_prompts.system_prompts import ESCLARECEDOR


def get_agente(contexto: ProjectContext):
    return criar_agente_de_modulo(ESCLARECEDOR, FinalSpecification, contexto,
                                  ferramentas.ESPECIFICACAO)
