from agents import ferramentas
from agents._base import criar_agente_de_modulo
from schemas import GeneratedFiles, ProjectContext
from system_prompts.system_prompts import IMPLEMENTADOR_FRONTEND


def get_agente(contexto: ProjectContext):
    return criar_agente_de_modulo(IMPLEMENTADOR_FRONTEND, GeneratedFiles, contexto,
                                  ferramentas.para("implementador_frontend"),
                                  etapa="implementador_frontend")
