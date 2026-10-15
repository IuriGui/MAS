"""Linha do tempo do que os agentes fazem no ambiente compartilhado.

    python -m scripts.acompanhar saida/f3/execucao_01/sistema          # até agora
    python -m scripts.acompanhar saida/f3/execucao_01/sistema --seguir # ao vivo
    python -m scripts.acompanhar saida/users --modulo Users            # um módulo

Junta, em ordem de horário:
  - acessos.jsonl   tools usadas e submissões de cada agente (leituras do ambiente)
  - mensagens.jsonl perguntas, respostas, bloqueios, revisões e pendências (F3)
  - ambiente.jsonl  o que o orquestrador publicou no ambiente
"""
import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional


def _linhas(arquivo: Path, desde: int) -> List[dict]:
    if not arquivo.is_file():
        return []
    texto = arquivo.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in texto[desde:] if l.strip()]


def _formatar(origem: str, e: dict) -> Optional[str]:
    if origem == "acessos":
        if e["acao"] in ("ask_agent", "report_blocker"):
            return None  # aparece em mensagens.jsonl
        args = ", ".join(f"{k}={str(v)[:50]!r}" for k, v in e["args"].items())
        if e["acao"].startswith("submit:"):
            ok = e["resultado"] == "approved"
            return (f"{e['etapa']:<26} {'✔ submeteu (aprovada)' if ok else '✘ submeteu (reprovada)'}"
                    + ("" if ok else f": {e['resultado'][10:160]}"))
        return (f"{e['etapa']:<26} lê  {e['acao']}({args}) → "
                f"{e['resultado_chars']} caracteres")
    if origem == "mensagens":
        seta = {"question": "pergunta a", "answer": "responde a", "blocker": "BLOQUEIA",
                "resolution": "revisou para", "unresolved": "PENDÊNCIA para"}[e["kind"]]
        return (f"{e['sender']:<26} {seta} {e['recipient']}: "
                f"{e['content'][:160].replace(chr(10), ' ')}")
    return f"{'orquestrador':<26} publica {', '.join(e['alvos'])[:120]} (autor: {e['autor']})"


def _hora(origem: str, e: dict) -> str:
    return e.get("quando") or e.get("sent_at") or ""


def coletar(raiz: Path, posicoes: Dict[Path, int], modulo: Optional[str]) -> List[tuple]:
    eventos = []
    for nome in ("acessos.jsonl", "mensagens.jsonl", "ambiente.jsonl"):
        origem = nome.split(".")[0]
        for arq in sorted(raiz.rglob(nome)):
            novos = _linhas(arq, posicoes.get(arq, 0))
            posicoes[arq] = posicoes.get(arq, 0) + len(novos)
            for e in novos:
                mod = e.get("modulo") if "modulo" in e else e.get("module")
                if modulo and mod and mod.lower() != modulo.lower():
                    continue
                eventos.append((_hora(origem, e), mod or "", origem, e))
    return sorted(eventos, key=lambda x: x[0])


def mostrar(eventos: List[tuple]) -> None:
    for hora, mod, origem, e in eventos:
        texto = _formatar(origem, e)
        if texto:
            print(f"{hora[11:19]}  {mod[:12]:<12}  {texto}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Linha do tempo dos agentes no ambiente")
    ap.add_argument("pasta", help="Pasta do projeto ou do módulo (ex.: saida/f3/execucao_01/sistema)")
    ap.add_argument("--modulo", help="Mostra só este módulo")
    ap.add_argument("--seguir", action="store_true", help="Continua mostrando o que chegar")
    args = ap.parse_args()
    raiz, posicoes = Path(args.pasta), {}
    mostrar(coletar(raiz, posicoes, args.modulo))
    while args.seguir:
        time.sleep(2)
        mostrar(coletar(raiz, posicoes, args.modulo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
