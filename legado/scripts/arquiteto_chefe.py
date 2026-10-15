"""Roda SÓ o Arquiteto-chefe sobre uma descrição de sistema.

    python -m legado.scripts.arquiteto_chefe                # exemplos/erp/sistema.md
    python -m legado.scripts.arquiteto_chefe caminho/sistema.md

Equivale a `python main.py --projeto <arquivo> --so-contexto`. A saída agora é
estruturada (ProjectContext) e validada em código; vai para
saida/<projeto>/0_contexto.json e contexto.md.
As execuções antigas (texto livre) continuam em scripts/saidas/arquiteto_chefe/.
"""
import sys
import time
from pathlib import Path

from config import SAIDA_DIR
from pipeline import slugify
from projeto import gerar_contexto

PADRAO = Path(__file__).resolve().parents[2] / "exemplos" / "erp" / "sistema.md"


if __name__ == "__main__":
    arquivo = Path(sys.argv[1]) if len(sys.argv) > 1 else PADRAO
    nome = arquivo.stem
    pasta = Path(SAIDA_DIR) / slugify(nome)
    inicio = time.time()
    gerar_contexto(nome, arquivo.read_text(encoding="utf-8"), pasta)
    print(f"Arquivos em: {pasta.resolve()}")
    print(f"{time.time() - inicio:.1f}s")
