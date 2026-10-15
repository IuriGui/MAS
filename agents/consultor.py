"""Agente em modo consulta (F3): o dono de um artefato responde a uma pergunta
de outro agente do módulo, sem mudar o artefato. Usa as regras do próprio papel,
o PROJECT CONTEXT e as tools de leitura do ambiente (sem comunicação, para não
encadear consultas)."""
from agents import ferramentas
from agents._base import criar_agente_de_modulo
from schemas import ConsultationAnswer, ProjectContext
from system_prompts.system_prompts import (ARQUITETO_SOFTWARE, CONSULTA, ESCLARECEDOR,
                                           MODELADOR_DE_DADOS, PLANEJADOR, REQUISITOS)

REGRAS = {
    "requisitos": REQUISITOS,
    "esclarecedor": ESCLARECEDOR,
    "modelador_dados": MODELADOR_DE_DADOS,
    "arquiteto_software": ARQUITETO_SOFTWARE,
    "planejador": PLANEJADOR,
}


def get_agente(etapa: str, contexto: ProjectContext):
    return criar_agente_de_modulo(REGRAS[etapa] + CONSULTA, ConsultationAnswer, contexto,
                                  ferramentas.para(etapa, comunicacao=False))
