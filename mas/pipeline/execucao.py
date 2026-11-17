"""Execução de um agente com validação em código e nova tentativa."""
import time
from typing import Callable, Dict, List, Optional, Tuple

from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from mas.codigo.layout import slugify
from mas.config import LIMITE_PASSOS_AGENTE, VALIDAR


class PipelineError(RuntimeError):
    pass


def _tokens(resultado: dict) -> Dict[str, object]:
    """Tokens e o que o agente fez numa chamada (passos, ferramentas, cache)."""
    total: Dict[str, object] = {"input_tokens": 0, "output_tokens": 0,
                                "cache_read_tokens": 0, "chamadas_modelo": 0,
                                "chamadas_tool": 0, "saida_tool_chars": 0,
                                "maior_saida_tool": 0, "tools_por_nome": {}}
    for m in resultado.get("messages", []):
        u = getattr(m, "usage_metadata", None) or {}
        total["input_tokens"] += u.get("input_tokens", 0)
        total["output_tokens"] += u.get("output_tokens", 0)
        total["cache_read_tokens"] += (u.get("input_token_details") or {}).get("cache_read", 0) or 0
        tipo = getattr(m, "type", "")
        if tipo == "ai":
            total["chamadas_modelo"] += 1
            for tc in getattr(m, "tool_calls", None) or []:
                nome = tc.get("name", "?")
                total["tools_por_nome"][nome] = total["tools_por_nome"].get(nome, 0) + 1
        elif tipo == "tool":
            total["chamadas_tool"] += 1
            tam = len(str(getattr(m, "content", "")))
            total["saida_tool_chars"] += tam
            total["maior_saida_tool"] = max(total["maior_saida_tool"], tam)
    return total


# campos somados a cada chamada (os demais: tools_por_nome soma por nome,
# maior_saida_tool guarda o máximo)
CAMPOS_SOMADOS = ("input_tokens", "output_tokens", "cache_read_tokens", "chamadas_modelo",
                  "chamadas_tool", "saida_tool_chars")


def _acumular(m: dict, tk: Dict[str, object]) -> None:
    for c in CAMPOS_SOMADOS:
        m[c] = m.get(c, 0) + tk[c]
    m["maior_saida_tool"] = max(m.get("maior_saida_tool", 0), tk["maior_saida_tool"])
    nomes = m.setdefault("tools_por_nome", {})
    for n, q in tk["tools_por_nome"].items():
        nomes[n] = nomes.get(n, 0) + q


def executar_agente(
    nome_etapa: str,
    agente,
    mensagem: str,
    validar: Callable[[BaseModel], List[str]],
    metricas: dict,
    max_tentativas: int,
    modulo: str = "",
    normalizar: Optional[Callable[[BaseModel], None]] = None,
    extrair: Optional[Callable[[dict], Optional[BaseModel]]] = None,
    limite_passos: int = LIMITE_PASSOS_AGENTE,
) -> Tuple[BaseModel, List[str]]:
    """Invoca o agente; se a validação falhar, re-executa com os erros anexados.

    `normalizar` corrige em código, antes de validar, erros mecânicos que não
    valem uma nova chamada (ex.: caminho trocado entre arquivos de um lote).
    `extrair` obtém o resultado da invocação quando ele não vem em
    `structured_response` (ex.: Implementador no sandbox, que grava em disco).
    É chamado mesmo se o agente estourar `limite_passos`.

    Limites (não há repetição sem fim):
    - no máximo `max_tentativas` invocações por etapa;
    - cada invocação tem no máximo `limite_passos` passos do grafo (padrão
      LIMITE_PASSOS_AGENTE), o que corta o retry interno do LangChain quando a
      saída estruturada não valida;
    - erros HTTP são repetidos no máximo MAX_RETRIES_API vezes (agentes/fabrica.py).
    """
    prompt = mensagem
    obj, erros = None, []
    for tentativa in range(1, max(1, max_tentativas) + 1):
        t0 = time.time()
        try:
            resultado = _invocar(agente, prompt, nome_etapa, modulo, tentativa,
                                 limite_passos)
        except GraphRecursionError:
            resultado = {"messages": [], "limite_passos": True}
        if resultado.get("limite_passos"):
            print(f"  [{nome_etapa}] tentativa {tentativa}: limite de {limite_passos} passos",
                  flush=True)
        if extrair:
            obj_tentativa = extrair(resultado)
        else:
            obj_tentativa = resultado.get("structured_response")

        tk = _tokens(resultado)
        m = metricas.setdefault(nome_etapa, {"tentativas": 0, "segundos": 0.0,
                                             "input_tokens": 0, "output_tokens": 0})
        m["tentativas"] += 1
        m["segundos"] = round(m["segundos"] + time.time() - t0, 2)
        _acumular(m, tk)

        if obj_tentativa is None:
            erros = ["A resposta não seguiu o formato estruturado exigido (JSON do schema)."]
            print(f"  [{nome_etapa}] tentativa {tentativa}: sem saída estruturada")
        else:
            obj = obj_tentativa
            if normalizar:
                normalizar(obj)
            erros = validar(obj) if VALIDAR else []
            if not erros:
                return obj, []
            print(f"  [{nome_etapa}] tentativa {tentativa} reprovada na validação: {erros}")
        prompt = (
            mensagem
            + "\n\nSUA RESPOSTA ANTERIOR FOI REPROVADA NA VALIDAÇÃO AUTOMÁTICA. "
            "Gere novamente corrigindo TODOS os pontos abaixo:\n- "
            + "\n- ".join(erros)
        )
    if obj is None:
        raise PipelineError(
            f"Etapa '{nome_etapa}': o modelo não produziu saída estruturada em "
            f"{max_tentativas} tentativa(s).")
    return obj, erros


def _invocar(agente, prompt: str, nome_etapa: str, modulo: str, tentativa: int,
             limite_passos: int = LIMITE_PASSOS_AGENTE) -> dict:
    return agente.invoke(
        {"messages": [{"role": "user", "content": prompt}]},
        config={
            "recursion_limit": limite_passos,
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
