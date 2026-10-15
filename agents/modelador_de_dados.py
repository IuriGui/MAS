from agents import ferramentas
from agents._base import criar_agente_de_modulo
from schemas import ModuleDataModel, ProjectContext
from system_prompts.system_prompts import MODELADOR_DE_DADOS


def get_agente(contexto: ProjectContext):
    return criar_agente_de_modulo(MODELADOR_DE_DADOS, ModuleDataModel, contexto,
                                  ferramentas.ESPECIFICACAO)
