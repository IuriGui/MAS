from agents import ferramentas
from agents._base import criar_agente_de_modulo
from schemas import ModuleArchitecture, ProjectContext
from system_prompts.system_prompts import ARQUITETO_SOFTWARE


def get_agente(contexto: ProjectContext):
    return criar_agente_de_modulo(ARQUITETO_SOFTWARE, ModuleArchitecture, contexto,
                                  ferramentas.para("arquiteto_software"),
                                  etapa="arquiteto_software")
