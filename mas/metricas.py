"""Métricas do projeto: tokens e tempo por módulo e por parte (especificação,
planejamento, backend, frontend), lidas dos metricas.json de cada pasta.

    python -m mas.metricas saida/sistema     # regenera metricas.md e metricas_projeto.json

`segundos` é o tempo dentro das chamadas aos agentes (inclui o que o agente roda
no sandbox: build, testes, servidor); não inclui subir o Docker nem a coleta.
"""
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from mas.codigo.layout import slugify

PARTES = ("contexto", "especificação", "planejamento", "backend", "frontend")
PARTE_DA_ETAPA = {
    "arquiteto_chefe": "contexto",
    "requisitos": "especificação",
    "esclarecedor": "especificação",
    "modelador_dados": "especificação",
    "esclarecedor_consulta": "especificação",
    "arquiteto_software": "especificação",
    "planejador": "planejamento",
    "planejador_base": "planejamento",
    "implementador_backend": "backend",
    "implementador_backend_base": "backend",
    "implementador_frontend": "frontend",
    "implementador_frontend_base": "frontend",
}
CAMPOS = ("input_tokens", "output_tokens", "segundos", "tentativas", "lotes",
          "chamadas_modelo", "cache_read_tokens", "reasoning_tokens",
          "input_tokens_fora_do_historico", "output_tokens_fora_do_historico")


def _vazio() -> Dict[str, float]:
    return {c: 0 for c in CAMPOS}


def _somar(destino: dict, origem: dict) -> None:
    for c in CAMPOS:
        destino[c] = round(destino[c] + origem.get(c, 0), 2)


def por_parte(metricas: dict) -> Dict[str, dict]:
    """{etapa: métricas} -> {parte: métricas somadas}, só as partes presentes."""
    partes: Dict[str, dict] = {}
    for etapa, m in metricas.items():
        if isinstance(m, dict):
            _somar(partes.setdefault(PARTE_DA_ETAPA.get(etapa, etapa), _vazio()), m)
    return partes


def _ler(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}


# metricas_backup.json: etapas reaproveitadas de execuções anteriores (artefatos
# iguais), gravadas à parte para não se misturarem com as da execução atual
ARQUIVOS = ("metricas.json", "metricas_backup.json")


def _ler_pasta(pasta: Path) -> Dict[str, dict]:
    """Etapas de todos os arquivos de métricas da pasta, somadas por parte."""
    partes: Dict[str, dict] = {}
    for arq in ARQUIVOS:
        for parte, m in por_parte(_ler(pasta / arq)).items():
            _somar(partes.setdefault(parte, _vazio()), m)
    return partes


def consolidar(pasta: Path, modulos: List[str]) -> dict:
    """Linhas: Arquiteto-chefe, base e cada módulo com metricas.json; mais o total."""
    linhas = []
    fontes = [("Arquiteto-chefe", por_parte(_ler(pasta / "metricas_contexto.json"))),
              ("Base compartilhada", _ler_pasta(pasta / "base"))]
    fontes += [(m, _ler_pasta(pasta / slugify(m))) for m in modulos]
    for nome, partes in fontes:
        if not partes:
            continue
        total = _vazio()
        for p in partes.values():
            _somar(total, p)
        linhas.append({"nome": nome, "partes": partes, "total": total})
    total_partes: Dict[str, dict] = {}
    total = _vazio()
    for linha in linhas:
        for parte, m in linha["partes"].items():
            _somar(total_partes.setdefault(parte, _vazio()), m)
        _somar(total, linha["total"])
    return {"linhas": linhas, "total": {"partes": total_partes, "total": total}}


def tempo(segundos: float) -> str:
    s = int(round(segundos))
    if s < 60:
        return f"{s} s"
    h, m = divmod(round(s / 60), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def tokens(n: float) -> str:
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f} mi".replace(".", ",")
    if n >= 1_000:
        return f"{n / 1_000:.0f} mil"
    return str(n)


def _ou_traco(n: float) -> str:
    return tokens(n) if n else "—"


def _celulas(m: dict) -> str:
    return (f"{tokens(m['input_tokens'])} | {_ou_traco(m.get('cache_read_tokens', 0))} | "
            f"{tokens(m['output_tokens'])} | {_ou_traco(m.get('reasoning_tokens', 0))} | "
            f"{tempo(m['segundos'])} | {int(m['tentativas'])} | {int(m['lotes']) or '—'} | "
            f"{int(m.get('chamadas_modelo', 0)) or '—'}")


def render_metricas(cons: dict, titulo: str = "Métricas do projeto") -> str:
    cab = ("| Entrada | Em cache | Saída | Raciocínio | Tempo | Tentativas | Lotes "
           "| Chamadas ao modelo |")
    sep = "|---|---|---|---|---|---|---|---|"
    out = [f"# {titulo}", "",
           "Tokens somados de todas as chamadas aos agentes. A entrada inclui a parte "
           "lida do cache do provedor (Em cache, registrada desde o relato 9); a "
           "entrada sem cache é a diferença. A saída inclui o raciocínio do modelo "
           "(Raciocínio, registrado desde o relato 10). "
           "Tempo: dentro das chamadas (inclui build, testes e servidor no sandbox). "
           "Tentativas: chamadas ao agente (uma por lote quando passa de primeira). "
           "Chamadas ao modelo: passos do agente, registrados desde o relato 9. "
           "\"—\": não registrado nas execuções anteriores (ou zero).", "",
           "## Total", "", "| Parte " + cab, "|---" + sep]
    tot = cons["total"]
    for parte in PARTES:
        if parte in tot["partes"]:
            out.append(f"| {parte} | {_celulas(tot['partes'][parte])} |")
    out.append(f"| **Total** | {_celulas(tot['total'])} |")
    t = tot["total"]
    if t.get("input_tokens_fora_do_historico") or t.get("output_tokens_fora_do_historico"):
        out += ["", "Fora do histórico do agente (subagentes, sumarização, chamadas "
                "cortadas pelo limite de passos), já incluídos nos totais: "
                f"{tokens(t['input_tokens_fora_do_historico'])} de entrada, "
                f"{tokens(t['output_tokens_fora_do_historico'])} de saída."]

    out += ["", "## Por módulo", "", "| Módulo " + cab, "|---" + sep]
    for linha in cons["linhas"]:
        out.append(f"| {linha['nome']} | {_celulas(linha['total'])} |")
    out.append(f"| **Total** | {_celulas(tot['total'])} |")

    out += ["", "## Por módulo e parte", "", "| Módulo | Parte " + cab, "|---|---" + sep]
    for linha in cons["linhas"]:
        for parte in PARTES:
            if parte in linha["partes"]:
                out.append(f"| {linha['nome']} | {parte} | {_celulas(linha['partes'][parte])} |")
    return "\n".join(out) + "\n"


def gravar_metricas(pasta: Path, modulos: List[str]) -> dict:
    cons = consolidar(pasta, modulos)
    (pasta / "metricas_projeto.json").write_text(
        json.dumps(cons, indent=2, ensure_ascii=False), encoding="utf-8")
    (pasta / "metricas.md").write_text(render_metricas(cons), encoding="utf-8")
    return cons


def _modulos_da_pasta(pasta: Path) -> List[str]:
    for arq in ("0_contexto.json", "projeto.json"):
        ordem = _ler(pasta / arq).get("build_order")
        if ordem:
            return ordem
    return sorted(p.name for p in pasta.iterdir()
                  if p.is_dir() and p.name != "base" and (p / "metricas.json").exists())


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("uso: python -m mas.metricas <pasta do projeto>  (ex.: saida/sistema)")
        return 2
    pasta = Path(argv[0])
    gravar_metricas(pasta, _modulos_da_pasta(pasta))
    print(f"Métricas em: {pasta}/metricas.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
