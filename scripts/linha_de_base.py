"""F0 — Linha de base: roda o projeto N vezes com a mesma entrada e resume as métricas.

    python -m scripts.linha_de_base --n 3                    # executa 3x e agrega
    python -m scripts.linha_de_base --n 3 --ate arquiteto_software   # só especificação
    python -m scripts.linha_de_base --so-agregar             # agrega o que já existe

Cada execução vai para <saida>/execucao_NN/ (continua a numeração existente) com
um execucao.json (commit, modelo, configuração, tempo, erro). O resumo vai para
<saida>/resumo.json e <saida>/resumo.md.

Padrões: exemplos/erp/sistema.md, exemplos/erp/modulos, saida/linha_de_base.
"""
import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

import config
import metricas
from pipeline import ETAPAS, PipelineError, carregar_contexto
from projeto import processar_projeto

RAIZ = Path(__file__).resolve().parent.parent
PADRAO_PROJETO = RAIZ / "exemplos" / "erp" / "sistema.md"
PADRAO_MODULOS = RAIZ / "exemplos" / "erp" / "modulos"


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _proxima(saida: Path) -> Path:
    existentes = [int(p.name.split("_")[1]) for p in saida.glob("execucao_*")
                  if p.name.split("_")[1].isdigit()]
    return saida / f"execucao_{max(existentes, default=0) + 1:02d}"


def executar_uma(args, saida: Path) -> Path:
    pasta = _proxima(saida)
    pasta.mkdir(parents=True)
    meta = {
        "inicio": datetime.now().isoformat(timespec="seconds"),
        "commit": _git("rev-parse", "HEAD"),
        "git_sujo": bool(_git("status", "--porcelain")),
        "modelo": config.MODEL,
        "config": {
            "MAX_TENTATIVAS": config.MAX_TENTATIVAS,
            "LIMITE_PASSOS_AGENTE": config.LIMITE_PASSOS_AGENTE,
            "IMPL_LOTE": config.IMPL_LOTE,
            "IMPL_LIMITE_CONTEXTO": config.IMPL_LIMITE_CONTEXTO,
            "VALIDAR": config.VALIDAR,
        },
        "entrada": {"projeto": str(args.projeto), "modulos_dir": str(args.modulos_dir),
                    "contexto": args.contexto, "ate": args.ate, "apenas": args.apenas},
        "erro": None,
    }
    print(f"\n##### {pasta.name} #####")
    t0 = time.time()
    try:
        processar_projeto(
            Path(args.projeto).stem, Path(args.projeto).read_text(encoding="utf-8"),
            modulos_dir=str(args.modulos_dir),
            contexto=carregar_contexto(args.contexto) if args.contexto else None,
            apenas=[m.strip() for m in args.apenas.split(",")] if args.apenas else None,
            ate=args.ate, saida_dir=str(pasta))
    except PipelineError as e:
        meta["erro"] = str(e)
        print(f"FALHA: {e}")
    finally:
        meta["segundos"] = round(time.time() - t0, 1)
        (pasta / "execucao.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return pasta


def agregar(saida: Path) -> dict:
    pastas = sorted(p for p in saida.glob("execucao_*") if p.is_dir())
    resumo = metricas.agregar([metricas.carregar_execucao(p) for p in pastas])
    (saida / "resumo.json").write_text(
        json.dumps(resumo, indent=2, ensure_ascii=False), encoding="utf-8")
    (saida / "resumo.md").write_text(metricas.markdown(resumo), encoding="utf-8")
    return resumo


def main() -> int:
    ap = argparse.ArgumentParser(description="Linha de base (F0): N execuções + resumo")
    ap.add_argument("--n", type=int, default=3, help="Quantas execuções (padrão: 3)")
    ap.add_argument("--projeto", default=PADRAO_PROJETO)
    ap.add_argument("--modulos-dir", default=PADRAO_MODULOS)
    ap.add_argument("--contexto", help="Fixa o PROJECT CONTEXT (pula o Arquiteto-chefe)")
    ap.add_argument("--apenas", help="Módulos separados por vírgula")
    ap.add_argument("--ate", choices=ETAPAS, default=ETAPAS[-1])
    ap.add_argument("--saida", default=str(Path(config.SAIDA_DIR) / "linha_de_base"))
    ap.add_argument("--so-agregar", action="store_true",
                    help="Não executa; só resume as execuções já existentes")
    args = ap.parse_args()

    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    if not args.so_agregar:
        for _ in range(args.n):
            executar_uma(args, saida)
    resumo = agregar(saida)
    print(f"\n{resumo['execucoes']} execução(ões) resumida(s) em {saida}/resumo.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
