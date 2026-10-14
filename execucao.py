"""Execução de um agente com validação em código e nova tentativa."""
import time
from typing import Callable, Dict, List, Optional, Tuple

from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

import metricas as med
from config import LIMITE_PASSOS_AGENTE, VALIDAR
from layout import slugify


class PipelineError(RuntimeError):
    pass


def _tokens(resultado: dict) -> Dict[str, int]:
    total = {"input_tokens": 0, "output_tokens": 0}
    for m in resultado.get("messages", []):
        u = getattr(m, "usage_metadata", None) or {}
        total["input_tokens"] += u.get("input_tokens", 0)
        total["output_tokens"] += u.get("output_tokens", 0)
    return total


def executar_agente(
    nome_etapa: str,
    agente,
    mensagem: str,
    validar: Callable[[BaseModel], List[str]],
    metricas: dict,
    max_tentativas: int,
    modulo: str = "",
    normalizar: Optional[Callable[[BaseModel], None]] = None,
) -> Tuple[BaseModel, List[str]]:
    """Invoca o agente; se a validação falhar, re-executa com os erros anexados.

    `normalizar` corrige em código, antes de validar, erros mecânicos que não
    valem uma nova chamada (ex.: caminho trocado entre arquivos de um lote).

    Limites (não há repetição sem fim):
    - no máximo `max_tentativas` invocações por etapa;
    - cada invocação tem no máximo LIMITE_PASSOS_AGENTE passos do grafo, o que
      corta o retry interno do LangChain quando a saída estruturada não valida;
    - erros HTTP são repetidos no máximo MAX_RETRIES_API vezes (agents/_base.py).
    """
    prompt = mensagem
    obj, erros = None, []
    for tentativa in range(1, max(1, max_tentativas) + 1):
        t0 = time.time()
        try:
            resultado = _invocar(agente, prompt, nome_etapa, modulo, tentativa)
        except GraphRecursionError:
            resultado = {"messages": []}
        if "structured_response" not in resultado:
            resultado["structured_response"] = None
        obj_tentativa = resultado["structured_response"]

        tk = _tokens(resultado)
        m = med.da_etapa(metricas, nome_etapa)
        m["tentativas"] += 1
        m["segundos"] = round(m["segundos"] + time.time() - t0, 2)
        m["input_tokens"] += tk["input_tokens"]
        m["output_tokens"] += tk["output_tokens"]
        m["chamadas_tool"] += med.chamadas_tool(resultado)

        if obj_tentativa is None:
            erros = ["A resposta não seguiu o formato estruturado exigido (JSON do schema)."]
            m["sem_saida"] += 1
            print(f"  [{nome_etapa}] tentativa {tentativa}: sem saída estruturada")
        else:
            obj = obj_tentativa
            if normalizar:
                normalizar(obj)
            erros = validar(obj) if VALIDAR else []
            if not erros:
                return obj, []
            m["reprovadas"] += 1
            med.registrar_erros(m, erros)
            print(f"  [{nome_etapa}] tentativa {tentativa} reprovada na validação: {erros}")
        prompt = (
            mensagem
            + "\n\nSUA RESPOSTA ANTERIOR FOI REPROVADA NA VALIDAÇÃO AUTOMÁTICA. "
            "Gere novamente corrigindo TODOS os pontos abaixo:\n- "
            + "\n- ".join(erros)
        )
    med.da_etapa(metricas, nome_etapa)["falhas_finais"] += 1
    if obj is None:
        raise PipelineError(
            f"Etapa '{nome_etapa}': o modelo não produziu saída estruturada em "
            f"{max_tentativas} tentativa(s).")
    return obj, erros


def _invocar(agente, prompt: str, nome_etapa: str, modulo: str, tentativa: int) -> dict:
    return agente.invoke(
        {"messages": [{"role": "user", "content": prompt}]},
        config={
            "recursion_limit": LIMITE_PASSOS_AGENTE,
            "run_name": f"{nome_etapa}_tentativa_{tentativa}",
            "tags": [nome_etapa, f"modulo:{slugify(modulo)}"],
            "metadata": {
                "etapa": nome_etapa,
                "modulo": modulo,
                "tentativa": tentativa,
                "reexecucao": tentativa > 1,
            },
        },
    )
