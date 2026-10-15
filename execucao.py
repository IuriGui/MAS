"""Execução de um agente com validação em código e nova tentativa."""
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

import metricas as med
from agents import ferramentas
from config import ESPERAS_API, LIMITE_PASSOS_AGENTE, VALIDAR
from layout import slugify


class PipelineError(RuntimeError):
    pass


_TRANSITORIO = re.compile(
    r"\b(429|500|502|503|504)\b|UNAVAILABLE|RESOURCE_EXHAUSTED|DEADLINE_EXCEEDED|"
    r"high demand|overloaded|temporarily|timed? ?out|"
    r"server disconnected|connection (reset|aborted|refused|error|closed)", re.I)

# quedas de rede (httpx/httpcore e da biblioteca padrão), pelo nome da classe
_REDE = {"RemoteProtocolError", "ConnectError", "ReadError", "WriteError",
         "ReadTimeout", "ConnectTimeout", "PoolTimeout", "NetworkError",
         "ConnectionError", "ConnectionResetError", "ConnectionAbortedError",
         "BrokenPipeError", "IncompleteRead"}


def erro_transitorio(exc: BaseException) -> bool:
    """Erro da API ou da rede que vale esperar e chamar de novo (sobrecarga,
    cota, 5xx, conexão derrubada)."""
    atual, vistas = exc, set()
    while atual is not None and id(atual) not in vistas:
        vistas.add(id(atual))
        codigo = getattr(atual, "status_code", None) or getattr(atual, "code", None)
        if (codigo in (429, 500, 502, 503, 504) or type(atual).__name__ in _REDE
                or _TRANSITORIO.search(str(atual))):
            return True
        atual = atual.__cause__ or atual.__context__
    return False


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
    leitura=None,
    lado: Optional[str] = None,
    canal=None,
) -> Tuple[BaseModel, List[str]]:
    """Invoca o agente; se a validação falhar, re-executa com os erros anexados.

    `normalizar` corrige em código, antes de validar, erros mecânicos que não
    valem uma nova chamada (ex.: caminho trocado entre arquivos de um lote).

    Agente autônomo (F2): cada invocação abre uma sessão (agents/ferramentas.py)
    com a `leitura` do ambiente, o módulo, o `lado` do código e a validação da
    etapa. O agente consulta o ambiente pelas tools e corrige as submissões
    reprovadas dentro do próprio loop. Esta função continua sendo o juiz final:
    revalida a saída e, se o orçamento do agente acabar sem saída aprovada,
    re-executa com os erros da última submissão reprovada.

    `canal` (F3): comunicação com os outros agentes do módulo (ask_agent,
    report_blocker). Os bloqueios ficam em `canal.bloqueios` para o chamador.

    Limites (não há repetição sem fim):
    - no máximo `max_tentativas` invocações por etapa;
    - cada invocação tem no máximo LIMITE_PASSOS_AGENTE passos do grafo (sem
      autonomia) ou LIMITE_CHAMADAS_AGENTE chamadas ao modelo (com autonomia);
    - erros HTTP são repetidos no máximo MAX_RETRIES_API vezes (agents/_base.py);
      se o erro continua e é transitório (sobrecarga da API), a invocação é refeita
      depois de cada espera de ESPERAS_API. Isso não conta como tentativa, e o tempo
      de espera fica fora de `segundos` (vai para `espera_api_s`).
    """
    def validar_submissao(o):
        if normalizar:
            normalizar(o)
        return validar(o)

    prompt = mensagem
    obj, erros = None, []
    for tentativa in range(1, max(1, max_tentativas) + 1):
        t0, espera = time.time(), 0.0
        for falha_api in range(len(ESPERAS_API) + 1):
            s = ferramentas.Sessao(etapa=nome_etapa, leitura=leitura, modulo=modulo, lado=lado,
                                   validar=validar_submissao if VALIDAR else None,
                                   canal=canal)
            aguardar = None
            with ferramentas.sessao(s):
                try:
                    resultado = _invocar(agente, prompt, nome_etapa, modulo, tentativa)
                except GraphRecursionError:
                    resultado = {"messages": []}
                except Exception as e:
                    if falha_api == len(ESPERAS_API) or not erro_transitorio(e):
                        raise
                    aguardar = ESPERAS_API[falha_api]
                    m = med.da_etapa(metricas, nome_etapa)
                    m["erros_api"] = m.get("erros_api", 0) + 1
                    print(f"  [{nome_etapa}] API indisponível ({str(e)[:80]}); "
                          f"nova chamada em {aguardar}s")
            if aguardar is None:
                break
            time.sleep(aguardar)
            espera += aguardar
        obj_tentativa = resultado.get("structured_response")
        if obj_tentativa is not None:
            obj_tentativa = ferramentas.para_base(obj_tentativa)

        tk = _tokens(resultado)
        m = med.da_etapa(metricas, nome_etapa)
        m["tentativas"] += 1
        m["segundos"] = round(m["segundos"] + time.time() - t0 - espera, 2)
        if espera:
            m["espera_api_s"] = m.get("espera_api_s", 0) + espera
        m["input_tokens"] += tk["input_tokens"]
        m["output_tokens"] += tk["output_tokens"]
        med.registrar_chamadas(m, resultado)
        for erros_internos in s.reprovacoes:
            m["reprovadas"] += 1
            med.registrar_erros(m, erros_internos)
        if s.reprovacoes:
            print(f"  [{nome_etapa}] tentativa {tentativa}: {len(s.reprovacoes)} "
                  "submissão(ões) reprovada(s) e devolvida(s) ao agente")

        if obj_tentativa is None and s.reprovacoes:
            # orçamento esgotado sem submissão aprovada: a última reprovada fica
            # como saída da tentativa (como uma resposta reprovada sem autonomia)
            obj, erros = s.ultima_reprovada, s.reprovacoes[-1]
            print(f"  [{nome_etapa}] tentativa {tentativa}: orçamento esgotado sem "
                  f"submissão aprovada: {erros}")
        elif obj_tentativa is None:
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
            # agente autônomo traz o próprio teto (agents/_base.py)
            "recursion_limit": getattr(agente, "limite_passos", LIMITE_PASSOS_AGENTE),
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
