"""Execução de um agente com validação em código e nova tentativa."""
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from mas.codigo.layout import slugify
from mas.config import LIMITE_PASSOS_AGENTE, VALIDAR


class PipelineError(RuntimeError):
    pass


# ------------------------------------------------------------ contabilização
# Tokens de uma chamada ao modelo, como o langchain-google-genai os informa:
# - input_tokens inclui a parte lida do cache do provedor (cache_read);
# - output_tokens inclui o raciocínio (reasoning).
_USO = (("input_tokens", lambda u: u.get("input_tokens", 0)),
        ("output_tokens", lambda u: u.get("output_tokens", 0)),
        ("cache_read_tokens", lambda u: (u.get("input_token_details") or {}).get("cache_read", 0)),
        ("reasoning_tokens", lambda u: (u.get("output_token_details") or {}).get("reasoning", 0)))


def _uso(u: dict) -> Dict[str, int]:
    return {campo: int(ler(u) or 0) for campo, ler in _USO}


class ContadorDeUso(BaseCallbackHandler):
    """Soma o uso de TODAS as chamadas ao modelo de uma invocação, inclusive as
    que não deixam mensagem no estado final do agente: subagentes (`task`),
    sumarização do deepagents e chamadas cortadas pelo limite de passos.

    Não depende de `response_metadata["model_name"]`, ao contrário do
    UsageMetadataCallbackHandler do langchain_core."""

    def __init__(self) -> None:
        super().__init__()
        self._trava = threading.Lock()
        self.uso: Dict[str, int] = {campo: 0 for campo, _ in _USO}
        self.chamadas = 0

    def on_llm_end(self, response, **kwargs: Any) -> None:
        for geracoes in response.generations:
            for g in geracoes:
                u = getattr(getattr(g, "message", None), "usage_metadata", None)
                if not u:
                    continue
                with self._trava:
                    self.chamadas += 1
                    for campo, n in _uso(u).items():
                        self.uso[campo] += n


def _tokens(resultado: dict) -> Dict[str, object]:
    """Tokens e o que o agente fez numa chamada (passos, ferramentas, cache)."""
    total: Dict[str, object] = {"input_tokens": 0, "output_tokens": 0,
                                "cache_read_tokens": 0, "reasoning_tokens": 0,
                                "chamadas_modelo": 0,
                                "chamadas_tool": 0, "saida_tool_chars": 0,
                                "maior_saida_tool": 0, "tools_por_nome": {}}
    total["leituras"] = {}
    alvos: Dict[str, str] = {}       # tool_call_id -> alvo (arquivo, busca ou comando)
    for m in resultado.get("messages", []):
        u = getattr(m, "usage_metadata", None) or {}
        for campo, n in _uso(u).items():
            total[campo] += n
        tipo = getattr(m, "type", "")
        if tipo == "ai":
            total["chamadas_modelo"] += 1
            for tc in getattr(m, "tool_calls", None) or []:
                nome = tc.get("name", "?")
                total["tools_por_nome"][nome] = total["tools_por_nome"].get(nome, 0) + 1
                alvo = _alvo(nome, tc.get("args") or {})
                if alvo:
                    alvos[tc.get("id", "")] = alvo
                    item = total["leituras"].setdefault(alvo, {"vezes": 0, "chars": 0})
                    item["vezes"] += 1
        elif tipo == "tool":
            total["chamadas_tool"] += 1
            tam = len(str(getattr(m, "content", "")))
            total["saida_tool_chars"] += tam
            total["maior_saida_tool"] = max(total["maior_saida_tool"], tam)
            alvo = alvos.get(getattr(m, "tool_call_id", "") or "")
            if alvo:
                total["leituras"][alvo]["chars"] += tam
    return total


def _alvo(nome: str, args: dict) -> str:
    """O que uma chamada de ferramenta leu ou rodou (para saber o que o agente procura).
    Escritas ficam de fora: o conteúdo escrito já é contado como saída do modelo."""
    if nome == "read_file":
        trecho = f" [{args.get('offset')}+{args.get('limit')}]" if args.get("offset") else ""
        return f"read_file {args.get('file_path', '?')}{trecho}"
    if nome in ("grep", "glob"):
        return f"{nome} {args.get('pattern', '?')!r} em {args.get('path') or '.'}"
    if nome == "ls":
        return f"ls {args.get('path', '?')}"
    if nome == "execute":
        return f"execute {str(args.get('command', '?'))[:160]}"
    return ""


def somar_leituras(destino: dict, origem: dict) -> None:
    for alvo, v in origem.items():
        d = destino.setdefault(alvo, {"vezes": 0, "chars": 0})
        d["vezes"] += v["vezes"]
        d["chars"] += v["chars"]


# campos somados a cada chamada (os demais: tools_por_nome soma por nome,
# maior_saida_tool guarda o máximo). Os "_fora_do_historico" são tokens que o
# ContadorDeUso viu e o estado final não tem (já incluídos em input/output).
CAMPOS_SOMADOS = ("input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens",
                  "input_tokens_fora_do_historico", "output_tokens_fora_do_historico",
                  "chamadas_modelo", "chamadas_tool", "saida_tool_chars")


def _acumular(m: dict, tk: Dict[str, object]) -> None:
    for c in CAMPOS_SOMADOS:
        m[c] = m.get(c, 0) + tk.get(c, 0)
    m["maior_saida_tool"] = max(m.get("maior_saida_tool", 0), tk.get("maior_saida_tool", 0))
    nomes = m.setdefault("tools_por_nome", {})
    for n, q in (tk.get("tools_por_nome") or {}).items():
        nomes[n] = nomes.get(n, 0) + q
    somar_leituras(m.setdefault("leituras", {}), tk.get("leituras") or {})


def somar_metricas(destino: dict, origem: dict) -> None:
    """Soma as métricas de uma etapa (ou de um lote) em outra."""
    destino["tentativas"] = destino.get("tentativas", 0) + origem.get("tentativas", 0)
    destino["segundos"] = round(destino.get("segundos", 0.0) + origem.get("segundos", 0.0), 2)
    _acumular(destino, origem)


def _com_contador(tk: Dict[str, object], contador: ContadorDeUso) -> Dict[str, object]:
    """Totais = o maior entre o estado final e o contador, por campo; a diferença
    de entrada e saída fica registrada como "fora do histórico"."""
    for campo, n in contador.uso.items():
        if campo in ("input_tokens", "output_tokens"):
            tk[f"{campo}_fora_do_historico"] = max(0, n - tk.get(campo, 0))
        tk[campo] = max(tk.get(campo, 0), n)
    return tk


def salvar_historico(caminho: Path, resultado: dict) -> None:
    """Grava as mensagens de uma invocação (para saber depois o que o agente leu,
    rodou e escreveu). Falhar aqui não interrompe o pipeline."""
    msgs = resultado.get("messages") or []
    if not msgs:
        return
    try:
        from langchain_core.messages import message_to_dict
        dados = {"limite_passos": bool(resultado.get("limite_passos")),
                 "mensagens": [m if isinstance(m, dict) else message_to_dict(m) for m in msgs]}
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1, default=str),
                           encoding="utf-8")
    except (OSError, TypeError, ValueError) as e:
        print(f"  AVISO: histórico não gravado em {caminho}: {e}")


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
    ao_invocar: Optional[Callable[[int, dict], None]] = None,
) -> Tuple[BaseModel, List[str]]:
    """Invoca o agente; se a validação falhar, re-executa com os erros anexados.

    `normalizar` corrige em código, antes de validar, erros mecânicos que não
    valem uma nova chamada (ex.: caminho trocado entre arquivos de um lote).
    `extrair` obtém o resultado da invocação quando ele não vem em
    `structured_response` (ex.: Implementador no sandbox, que grava em disco).
    É chamado mesmo se o agente estourar `limite_passos`.
    `ao_invocar(tentativa, resultado)` roda depois de cada invocação (ex.: gravar
    o histórico de mensagens).

    Tokens: somados das mensagens do estado final e conferidos com um
    ContadorDeUso, que vê também as chamadas que não ficam no estado.

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
        contador = ContadorDeUso()
        try:
            resultado = _invocar(agente, prompt, nome_etapa, modulo, tentativa,
                                 limite_passos, callbacks=[contador])
        except GraphRecursionError:
            resultado = {"messages": [], "limite_passos": True}
        if resultado.get("limite_passos"):
            print(f"  [{nome_etapa}] tentativa {tentativa}: limite de {limite_passos} passos",
                  flush=True)

        tk = _com_contador(_tokens(resultado), contador)
        m = metricas.setdefault(nome_etapa, {"tentativas": 0, "segundos": 0.0,
                                             "input_tokens": 0, "output_tokens": 0})
        m["tentativas"] += 1
        m["segundos"] = round(m["segundos"] + time.time() - t0, 2)
        _acumular(m, tk)
        if ao_invocar:
            ao_invocar(tentativa, resultado)

        if extrair:
            obj_tentativa = extrair(resultado)
        else:
            obj_tentativa = resultado.get("structured_response")

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
             limite_passos: int = LIMITE_PASSOS_AGENTE,
             callbacks: Optional[list] = None) -> dict:
    return agente.invoke(
        {"messages": [{"role": "user", "content": prompt}]},
        config={
            "callbacks": callbacks or [],
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
