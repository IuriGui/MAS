from agents._base import criar_agente_de_modulo
from schemas import ModuleAnalysis, ProjectContext
from system_prompts.system_prompts import REQUISITOS


def get_agente(contexto: ProjectContext):
    return criar_agente_de_modulo(REQUISITOS, ModuleAnalysis, contexto)
