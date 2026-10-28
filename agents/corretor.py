"""Implementador em modo correção de build: corrige os erros de build nos próprios
arquivos (modo "correcao") ou atende o pedido de mudança de outro agente num
arquivo seu (modo "pedido"). Usa as regras do próprio papel, o PROJECT CONTEXT e
as tools de leitura; só quem corrige pode pedir mudança (request_change), para
não encadear pedidos."""
from agents import ferramentas
from agents._base import criar_agente_de_modulo
from schemas import BuildFix, ProjectContext
from system_prompts.system_prompts import (CORRECAO, IMPLEMENTADOR_BACKEND,
                                           IMPLEMENTADOR_FRONTEND, PEDIDO_MUDANCA)

REGRAS = {"backend": IMPLEMENTADOR_BACKEND, "frontend": IMPLEMENTADOR_FRONTEND}


def get_agente(lado: str, contexto: ProjectContext, modo: str = "correcao"):
    if modo == "correcao":
        regras, tools = REGRAS[lado] + CORRECAO, ferramentas.CORRECAO + (
            ferramentas.request_change,)
    else:
        regras, tools = REGRAS[lado] + PEDIDO_MUDANCA, ferramentas.CORRECAO
    return criar_agente_de_modulo(regras, BuildFix, contexto, tools,
                                  etapa=f"implementador_{lado}")
