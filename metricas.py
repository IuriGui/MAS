"""Métricas das execuções.

Registro (usado por execucao.py), por etapa, em metricas[etapa]:
  tentativas       invocações do agente (cada lote e cada nova tentativa contam)
  reprovadas       tentativas com saída estruturada reprovada na validação
  sem_saida        tentativas sem saída estruturada
  falhas_finais    vezes em que a etapa (ou um lote) esgotou as tentativas
  chamadas_tool    tool calls do agente, fora a da saída estruturada
  tools_por_nome   chamadas de cada tool
  chamadas_modelo  respostas do modelo (com autonomia, uma invocação tem várias)
  erros_por_regra  quantas vezes cada regra de validação reprovou uma saída
  input_tokens, output_tokens, segundos; lotes (só nos Implementadores)

Agregação (usada por scripts/linha_de_base.py): lê as pastas de várias
execuções do projeto e resume média, desvio, mínimo e máximo por etapa.
"""
import json
import re
import statistics
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from layout import slugify

CAMPOS = ("tentativas", "reprovadas", "sem_saida", "falhas_finais", "chamadas_tool",
          "chamadas_modelo", "input_tokens", "output_tokens", "segundos", "lotes")


# ------------------------------------------------------------- registro
def da_etapa(metricas: dict, etapa: str) -> dict:
    """metricas[etapa], com todos os campos (inclusive em métricas antigas)."""
    m = metricas.setdefault(etapa, {})
    for campo in CAMPOS:
        if campo != "lotes":
            m.setdefault(campo, 0.0 if campo == "segundos" else 0)
    m.setdefault("erros_por_regra", {})
    m.setdefault("tools_por_nome", {})
    return m


_ASPAS = re.compile(r"'[^']*'|\"[^\"]*\"")
_LISTA = re.compile(r"\[[^\]]*\]")
_CADEIA = re.compile(r"\S+(?: -> \S+)+")
_NUMERO = re.compile(r"\b\d+\b")
_PREFIXOS = (
    (re.compile(r"^Endpoint \S+ \S+"), "Endpoint _"),
    (re.compile(r"^Relationship \S+ \S+ \S+"), "Relationship _"),
    (re.compile(r"^N:N relationship \S+"), "N:N relationship _"),
)


def regra(erro: str) -> str:
    """Tira de uma mensagem de erro os valores concretos, deixando a regra.

    "Table 'users' is missing required columns: ['created_at']."
      -> "Table '_' is missing required columns: [_]."
    """
    r = erro.strip()
    for padrao, troca in _PREFIXOS:
        r = padrao.sub(troca, r)
    r = _ASPAS.sub("'_'", r)
    r = _LISTA.sub("[_]", r)
    r = _CADEIA.sub("_", r)
    r = _NUMERO.sub("N", r)
    return re.sub(r"\s+", " ", r)[:160]


def registrar_erros(m: dict, erros: Iterable[str]) -> None:
    contagem = m.setdefault("erros_por_regra", {})
    for e in erros:
        chave = regra(e)
        contagem[chave] = contagem.get(chave, 0) + 1


@lru_cache(maxsize=1)
def _nomes_schemas() -> frozenset:
    import schemas
    from pydantic import BaseModel
    return frozenset(n for n, o in vars(schemas).items()
                     if isinstance(o, type) and issubclass(o, BaseModel))


def _tools_chamadas(resultado: dict) -> List[str]:
    """Nomes das tools chamadas, sem a tool da saída estruturada
    (no ToolStrategy do LangChain ela tem o nome do schema)."""
    ignorar = _nomes_schemas()
    return [c.get("name") for msg in resultado.get("messages", [])
            for c in (getattr(msg, "tool_calls", None) or [])
            if c.get("name") not in ignorar]


def chamadas_tool(resultado: dict) -> int:
    return len(_tools_chamadas(resultado))


def registrar_chamadas(m: dict, resultado: dict) -> None:
    """Soma em m as tool calls (total e por nome) e as respostas do modelo."""
    nomes = _tools_chamadas(resultado)
    m["chamadas_tool"] += len(nomes)
    por_nome = m.setdefault("tools_por_nome", {})
    for n in nomes:
        por_nome[n] = por_nome.get(n, 0) + 1
    m["chamadas_modelo"] = m.get("chamadas_modelo", 0) + sum(
        1 for msg in resultado.get("messages", []) if getattr(msg, "type", "") == "ai")


# ------------------------------------------------------------- leitura
def _ler(caminho: Path) -> Optional[dict]:
    if not caminho.is_file():
        return None
    return json.loads(caminho.read_text(encoding="utf-8"))


_ETAPA_FALHA = re.compile(r"Etapa '([^']+)'")


def carregar_execucao(pasta: Path) -> dict:
    """Uma execução do projeto: métricas por unidade, status dos módulos e meta.

    Unidades: "projeto" (Arquiteto-chefe), "base" e cada módulo.
    """
    pasta = Path(pasta)
    meta = _ler(pasta / "execucao.json") or {}
    unidades: Dict[str, dict] = {}
    status: Dict[str, dict] = {}
    for raiz in sorted({p.parent for p in pasta.rglob("metricas_contexto.json")}
                       | {p.parent for p in pasta.rglob("projeto.json")}):
        chefe = _ler(raiz / "metricas_contexto.json")
        if chefe:
            unidades["projeto"] = chefe
        base = _ler(raiz / "base" / "metricas.json")
        if base:
            unidades["base"] = base
        projeto = _ler(raiz / "projeto.json") or {}
        for nome, st in projeto.get("modulos", {}).items():
            falha = _ETAPA_FALHA.search(st.get("erro", ""))
            status[nome] = {"status": st.get("status"),
                            "etapa_falha": falha.group(1) if falha else None}
            m = _ler(raiz / slugify(nome) / "metricas.json")
            if m:
                unidades[nome] = m
    # sem "segundos" no execucao.json: execução em andamento ou interrompida
    completa = "segundos" in meta
    return {"nome": pasta.name, "meta": meta, "unidades": unidades, "status": status,
            "completa": completa}


# ------------------------------------------------------------- agregação
def _estat(valores: List[float]) -> dict:
    if not valores:
        return {"media": 0, "desvio": 0, "min": 0, "max": 0}
    return {
        "media": round(statistics.mean(valores), 2),
        "desvio": round(statistics.stdev(valores), 2) if len(valores) > 1 else 0,
        "min": round(min(valores), 2),
        "max": round(max(valores), 2),
    }


def _por_etapa(execucao: dict) -> Dict[str, dict]:
    """Soma, numa execução, os campos de cada etapa em todas as unidades."""
    total: Dict[str, dict] = {}
    for metricas in execucao["unidades"].values():
        for etapa, m in metricas.items():
            t = total.setdefault(etapa, {c: 0 for c in CAMPOS})
            for c in CAMPOS:
                t[c] += m.get(c, 0)
    return total


def agregar(execucoes: List[dict]) -> dict:
    """Resume as execuções completas; as incompletas só são listadas."""
    incompletas = [e["nome"] for e in execucoes if not e.get("completa", True)]
    execucoes = [e for e in execucoes if e.get("completa", True)]
    n = len(execucoes)
    por_exec = [_por_etapa(e) for e in execucoes]

    ordem: List[str] = []
    for pe in por_exec:
        ordem += [e for e in pe if e not in ordem]
    etapas = {}
    for etapa in ordem:
        linhas = [pe.get(etapa, {c: 0 for c in CAMPOS}) for pe in por_exec]
        resumo = {c: _estat([l[c] for l in linhas]) for c in CAMPOS}
        tent = sum(l["tentativas"] for l in linhas)
        ruins = sum(l["reprovadas"] + l["sem_saida"] for l in linhas)
        resumo["taxa_reprovacao"] = round(ruins / tent, 3) if tent else 0
        etapas[etapa] = resumo

    resumo_exec = []
    for e, pe in zip(execucoes, por_exec):
        st = e["status"]
        resumo_exec.append({
            "nome": e["nome"],
            "modulos_ok": sum(1 for s in st.values() if s["status"] == "ok"),
            "modulos": len(st),
            "erro": e["meta"].get("erro"),
            "segundos_relogio": e["meta"].get("segundos"),
            **{c: round(sum(m[c] for m in pe.values()), 2)
               for c in ("tentativas", "reprovadas", "sem_saida", "falhas_finais",
                         "chamadas_tool", "chamadas_modelo", "input_tokens",
                         "output_tokens", "segundos")},
        })

    modulos: Dict[str, dict] = {}
    for e in execucoes:
        for nome, s in e["status"].items():
            md = modulos.setdefault(nome, {"ok": 0, "execucoes": 0, "etapas_falha": {}})
            md["execucoes"] += 1
            if s["status"] == "ok":
                md["ok"] += 1
            elif s["etapa_falha"]:
                md["etapas_falha"][s["etapa_falha"]] = (
                    md["etapas_falha"].get(s["etapa_falha"], 0) + 1)

    regras: Dict[tuple, int] = {}
    for e in execucoes:
        for metricas in e["unidades"].values():
            for etapa, m in metricas.items():
                for r, q in m.get("erros_por_regra", {}).items():
                    regras[(etapa, r)] = regras.get((etapa, r), 0) + q
    top = sorted(regras.items(), key=lambda kv: (-kv[1], kv[0]))

    tools: Dict[tuple, int] = {}
    for e in execucoes:
        for metricas in e["unidades"].values():
            for etapa, m in metricas.items():
                for t, q in m.get("tools_por_nome", {}).items():
                    tools[(etapa, t)] = tools.get((etapa, t), 0) + q

    metas = [e["meta"] for e in execucoes]
    return {
        "execucoes": n,
        "incompletas": incompletas,
        "commits": sorted({m.get("commit") for m in metas if m.get("commit")}),
        "modelos": sorted({m.get("modelo") for m in metas if m.get("modelo")}),
        "config": next((m.get("config") for m in metas if m.get("config")), {}),
        "por_execucao": resumo_exec,
        "por_etapa": etapas,
        "por_modulo": modulos,
        "erros_por_regra": [{"etapa": et, "regra": r, "ocorrencias": q}
                            for (et, r), q in top],
        "tools_por_etapa": [{"etapa": et, "tool": t, "chamadas": q}
                            for (et, t), q in sorted(tools.items())],
        # Comunicação entre agentes: não existe na linha de base (planejada na F3)
        "mensagens_entre_agentes": 0,
    }


# ------------------------------------------------------------- relatório
def _ms(e: dict, casas: int = 1) -> str:
    if not e["desvio"]:
        return f"{e['media']:.{casas}f}"
    return f"{e['media']:.{casas}f} ± {e['desvio']:.{casas}f}"


def _milhar(x: float) -> str:
    return f"{x / 1000:.1f} mil" if x >= 1000 else f"{x:.0f}"


def markdown(r: dict) -> str:
    cfg = ", ".join(f"{k}={v}" for k, v in r["config"].items()) or "-"
    linhas = [
        "# Linha de base",
        "",
        f"- Execuções: {r['execucoes']}"
        + (f" (ignoradas por estarem incompletas: {', '.join(r['incompletas'])})"
           if r.get("incompletas") else ""),
        f"- Modelo(s): {', '.join(r['modelos']) or '-'}",
        f"- Commit(s): {', '.join(c[:10] for c in r['commits']) or '-'}",
        f"- Configuração: {cfg}",
        "",
        "## Por execução",
        "",
        "| Execução | Módulos ok | Tentativas | Reprovadas | Sem saída | "
        "Tokens entrada | Tokens saída | Tempo dos agentes (s) | Tempo total (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for e in r["por_execucao"]:
        ok = f"{e['modulos_ok']}/{e['modulos']}" + (" (erro)" if e["erro"] else "")
        linhas.append(
            f"| {e['nome']} | {ok} | {e['tentativas']} | {e['reprovadas']} | "
            f"{e['sem_saida']} | {_milhar(e['input_tokens'])} | "
            f"{_milhar(e['output_tokens'])} | {e['segundos']:.0f} | "
            f"{e['segundos_relogio'] or '-'} |")
    linhas += [
        "",
        "## Por etapa (média ± desvio entre execuções)",
        "",
        "| Etapa | Tentativas | Reprovadas | Sem saída | Falhas finais | "
        "Taxa de reprovação | Chamadas ao modelo | Tokens entrada | Tokens saída | "
        "Tempo (s) | Chamadas de tool |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for etapa, m in r["por_etapa"].items():
        linhas.append(
            f"| {etapa} | {_ms(m['tentativas'])} | {_ms(m['reprovadas'])} | "
            f"{_ms(m['sem_saida'])} | {_ms(m['falhas_finais'])} | "
            f"{m['taxa_reprovacao']:.0%} | {_ms(m['chamadas_modelo'])} | "
            f"{_ms(m['input_tokens'], 0)} | "
            f"{_ms(m['output_tokens'], 0)} | {_ms(m['segundos'], 0)} | "
            f"{_ms(m['chamadas_tool'], 0)} |")
    linhas += ["", "## Por módulo", "", "| Módulo | Sucessos | Etapas em que falhou |",
               "|---|---|---|"]
    for nome, m in r["por_modulo"].items():
        falhas = ", ".join(f"{e} ({q}x)" for e, q in m["etapas_falha"].items()) or "-"
        linhas.append(f"| {nome} | {m['ok']}/{m['execucoes']} | {falhas} |")
    linhas += ["", "## Regras de validação que mais reprovaram", ""]
    if r["erros_por_regra"]:
        linhas += ["| Etapa | Regra | Ocorrências |", "|---|---|---|"]
        for x in r["erros_por_regra"][:20]:
            linhas.append(f"| {x['etapa']} | {x['regra'].replace('|', '/')} | "
                          f"{x['ocorrencias']} |")
    else:
        linhas.append("Nenhuma reprovação registrada.")
    total_tool = sum(e["chamadas_tool"] for e in r["por_execucao"])
    linhas += [
        "",
        "## Interação entre agentes",
        "",
        f"- Chamadas de tool: {total_tool}",
        f"- Mensagens entre agentes: {r['mensagens_entre_agentes']} "
        "(previstas na F3)",
        "",
    ]
    if r.get("tools_por_etapa"):
        linhas += ["| Etapa | Tool | Chamadas |", "|---|---|---|"]
        linhas += [f"| {x['etapa']} | {x['tool']} | {x['chamadas']} |"
                   for x in r["tools_por_etapa"]]
        linhas.append("")
    return "\n".join(linhas)
