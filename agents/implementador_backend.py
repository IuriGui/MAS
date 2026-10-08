from agents._base import criar_agente_de_modulo
from schemas import GeneratedFiles, ProjectContext
from system_prompts.system_prompts import IMPLEMENTADOR_BACKEND


def get_agente(contexto: ProjectContext):
    return criar_agente_de_modulo(IMPLEMENTADOR_BACKEND, GeneratedFiles, contexto)
