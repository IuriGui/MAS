"""Uso:
  Um módulo (usa o PROJECT CONTEXT de exemplos/contexto_erp.json ou --contexto):
    python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md  # até o código
    python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md --ate arquiteto_software
    python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md --de planejador
    python main.py --nome "Inventory" --arquivo estoque.md --outros resumo_outros.md

  Projeto inteiro (Arquiteto-chefe + todos os módulos na ordem de construção):
    python main.py --projeto exemplos/erp/sistema.md --modulos-dir exemplos/erp/modulos
    python main.py --projeto exemplos/erp/sistema.md --contexto saida/sistema/0_contexto.json \\
                   --apenas Users,Customers
    python main.py --projeto exemplos/erp/sistema.md --so-contexto
"""
import argparse
from pathlib import Path

from pipeline import ETAPAS, PipelineError, carregar_contexto, processar_modulo


def _metricas(metricas: dict, recuo: str = "") -> None:
    for etapa, m in metricas.items():
        print(f"{recuo}{etapa}: {m['segundos']}s, {m['input_tokens']} tok in / "
              f"{m['output_tokens']} tok out, {m['tentativas']} tentativa(s)")


def _alertas(alertas, recuo: str = "") -> None:
    if alertas:
        print(f"{recuo}ATENÇÃO — itens para revisão humana:")
        for a in alertas:
            print(f"{recuo}  • {a}")


def rodar_modulo(args, contexto) -> int:
    if not args.nome or not args.arquivo:
        print("Informe --nome e --arquivo (ou use --projeto).")
        return 2
    descricao = Path(args.arquivo).read_text(encoding="utf-8")
    outros = Path(args.outros).read_text(encoding="utf-8") if args.outros else None
    try:
        res = processar_modulo(args.nome, descricao, outros, ate=args.ate, de=args.de,
                               contexto=contexto)
    except PipelineError as e:
        print(f"\nFALHA: {e}")
        return 1
    print(f"\nArtefatos em: {res['pasta']}/")
    if "arquivos_backend" in res or "arquivos_frontend" in res:
        print(f"Código em: {res['codigo']}/")
    spec = res.get("especificacao")
    _alertas(spec.review_alerts if spec else [])
    _metricas(res["metricas"])
    return 0


def rodar_projeto(args, contexto) -> int:
    from projeto import gerar_contexto, processar_projeto
    from pipeline import slugify
    from config import SAIDA_DIR

    descricao = Path(args.projeto).read_text(encoding="utf-8")
    nome = args.nome or Path(args.projeto).stem
    try:
        if args.so_contexto:
            pasta = Path(SAIDA_DIR) / slugify(nome)
            gerar_contexto(nome, descricao, pasta)
            print(f"\nContexto em: {pasta}/0_contexto.json")
            return 0
        apenas = [m.strip() for m in args.apenas.split(",")] if args.apenas else None
        res = processar_projeto(nome, descricao, modulos_dir=args.modulos_dir,
                                contexto=contexto, apenas=apenas, ate=args.ate,
                                de=args.de)
    except PipelineError as e:
        print(f"\nFALHA: {e}")
        return 1

    print(f"\nArtefatos em: {res['pasta']}/")
    falhas = 0
    for nome_mod, st in res["modulos"].items():
        print(f"\n[{st['status']}] {nome_mod}")
        if st["status"] != "ok":
            falhas += 1
            continue
        _alertas(st["review_alerts"], "  ")
        _metricas(st["metricas"], "  ")
    return 1 if falhas else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Pipeline de especificação (módulo ou projeto)")
    ap.add_argument("--nome", help="Nome do módulo (ou do projeto, com --projeto)")
    ap.add_argument("--arquivo", help="Arquivo com a descrição do módulo")
    ap.add_argument("--outros", help="Resumo dos outros módulos (padrão: gerado do contexto)")
    ap.add_argument("--projeto", help="Descrição do sistema com seção MODULES")
    ap.add_argument("--modulos-dir", help="Pasta com <modulo>.md detalhando cada módulo")
    ap.add_argument("--apenas", help="Lista de módulos separados por vírgula")
    ap.add_argument("--so-contexto", action="store_true",
                    help="Com --projeto: roda só o Arquiteto-chefe")
    ap.add_argument("--contexto", help="PROJECT CONTEXT já gerado (0_contexto.json)")
    ap.add_argument("--ate", choices=ETAPAS, default=ETAPAS[-1],
                    help="Última etapa a executar em cada módulo (padrão: até o frontend)")
    ap.add_argument("--de", choices=ETAPAS, default=ETAPAS[0],
                    help="Retoma a partir desta etapa, lendo do disco as anteriores")
    args = ap.parse_args()

    contexto = carregar_contexto(args.contexto) if args.contexto else None
    if args.projeto:
        return rodar_projeto(args, contexto)
    return rodar_modulo(args, contexto)


if __name__ == "__main__":
    raise SystemExit(main())
