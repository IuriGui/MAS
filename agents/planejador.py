from agents import ferramentas
from agents._base import criar_agente_de_modulo
from schemas import ImplementationPlan, ProjectContext
from system_prompts.system_prompts import PLANEJADOR


def get_agente(contexto: ProjectContext):
    return criar_agente_de_modulo(PLANEJADOR, ImplementationPlan, contexto,
                                  ferramentas.para("planejador"),
                                  etapa="planejador")
