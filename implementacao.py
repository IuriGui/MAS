"""Etapas de implementação: Planejador -> Implementador backend -> frontend.

- A BASE (esqueleto compartilhado) é planejada e implementada uma vez por
  projeto; os módulos se encaixam nela.
- Cada Implementador gera o plano em LOTES (IMPL_LOTE arquivos por chamada),
  em ordem topológica de depends_on; cada lote recebe o conteúdo já gerado dos
  arquivos de que depende.
- Cada lote é validado em código (validation.validar_arquivos) e só então
  gravado em disco, em <codigo>/backend e <codigo>/frontend.
- A migration do módulo é gerada sem LLM (ddl.py).
"""
import json
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Set

import layout
import validation
from config import IMPL_LIMITE_CONTEXTO, IMPL_LOTE, MAX_TENTATIVAS
from ddl import gerar_ddl, usa_sql
from execucao import PipelineError, executar_agente
from schemas import (FinalSpecification, GeneratedFiles, ImplementationPlan,
                     ModuleArchitecture, ModuleDataModel, PlannedFile,
                     ProjectContext)
from system_prompts import templates

LADOS = ("backend", "frontend")
_IGNORAR = {"node_modules", "target", "build", "dist", ".git", ".gradle", ".idea"}


# ------------------------------------------------------------- disco
def caminhos_em_disco(raiz: Path) -> Set[str]:
    if not raiz.exists():
        return set()
    return {p.relative_to(raiz).as_posix() for p in raiz.rglob("*")
            if p.is_file() and not _IGNORAR & set(p.relative_to(raiz).parts)}


def _destino(raiz: Path, caminho: str) -> Path:
    motivo = layout.caminho_invalido(caminho)
    destino = (raiz / caminho).resolve()
    if motivo or not destino.is_relative_to(raiz.resolve()):
        raise PipelineError(f"Caminho recusado ao gravar: '{caminho}' ({motivo or 'fora da raiz'}).")
    return destino


def gravar(raiz: Path, gerados: GeneratedFiles) -> List[str]:
    gravados = []
    for f in gerados.files:
        destino = _destino(raiz, f.path)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(layout.limpar_conteudo(f.content), encoding="utf-8")
        gravados.append(f.path)
    return gravados


def _salvar_json(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------- lotes
def montar_lotes(arquivos: List[PlannedFile], tamanho: int = IMPL_LOTE) -> List[List[PlannedFile]]:
    """Agrupa em lotes respeitando depends_on: um arquivo só entra num lote
    quando suas dependências (dentro do plano) já foram geradas ou estão no
    mesmo lote. Sem saída possível (ciclo), o restante vai na ordem do plano."""
    tamanho = max(1, tamanho)  # tamanho 0 faria o laço nunca avançar
    planejados = {f.path for f in arquivos}
    pendentes = list(arquivos)
    feitos: Set[str] = set()
    lotes = []
    while pendentes:
        lote: List[PlannedFile] = []
        no_lote: Set[str] = set()
        for f in pendentes:
            deps = {d for d in f.depends_on if d in planejados}
            if deps <= feitos | no_lote:
                lote.append(f)
                no_lote.add(f.path)
                if len(lote) == tamanho:
                    break
        if not lote:
            lote = pendentes[:tamanho]
        lotes.append(lote)
        feitos |= {f.path for f in lote}
        pendentes = [f for f in pendentes if f.path not in feitos]
    return lotes


def conteudo_dependencias(raiz: Path, lote: List[PlannedFile],
                          limite: int = IMPL_LIMITE_CONTEXTO) -> str:
    """Conteúdo dos arquivos (já gravados) de que o lote depende."""
    no_lote = {f.path for f in lote}
    partes, total = [], 0
    vistos: Set[str] = set()
    for f in lote:
        for d in f.depends_on:
            if d in no_lote or d in vistos or not (raiz / d).is_file():
                continue
            vistos.add(d)
            texto = (raiz / d).read_text(encoding="utf-8")
            bloco = f"=== {d} ===\n{texto}"
            if total + len(bloco) > limite:
                partes.append(f"=== {d} === (omitido: limite de contexto)")
                continue
            partes.append(bloco)
            total += len(bloco)
    return "\n\n".join(partes)


def interfaces_externas(raiz_backend: Path, arq: ModuleArchitecture,
                        prefixos_proprios: Iterable[str],
                        limite: int = IMPL_LIMITE_CONTEXTO) -> str:
    """Código das interfaces públicas de outros módulos que este módulo usa."""
    nomes = {d.public_interface.split(".")[-1].strip() for d in arq.module_dependencies}
    nomes = {n for n in nomes if n}
    partes, total = [], 0
    for caminho in sorted(caminhos_em_disco(raiz_backend)):
        if caminho.startswith(tuple(prefixos_proprios)):
            continue
        if Path(caminho).stem in nomes:
            bloco = f"=== {caminho} ===\n{(raiz_backend / caminho).read_text(encoding='utf-8')}"
            if total + len(bloco) <= limite:
                partes.append(bloco)
                total += len(bloco)
    return "\n\n".join(partes)


def executar_lotes(
    etapa: str,
    agente,
    arquivos: List[PlannedFile],
    raiz: Path,
    montar_mensagem: Callable[[List[PlannedFile], str, int, int], str],
    conhecidos: Callable[[], validation.Conhecidos],
    pasta: Path,
    metricas: dict,
    max_tentativas: int,
    modulo: str,
) -> List[dict]:
    """Gera os arquivos lote a lote, validando e gravando cada lote."""
    lotes = montar_lotes(arquivos)
    manifesto = []
    for i, lote in enumerate(lotes, 1):
        print(f"  [{etapa}] lote {i}/{len(lotes)}: {len(lote)} arquivo(s)")
        msg = montar_mensagem(lote, conteudo_dependencias(raiz, lote), i, len(lotes))
        k = conhecidos()
        gerados, erros = executar_agente(
            etapa, agente, msg, lambda g: validation.validar_arquivos(g, lote, k),
            metricas, max_tentativas, modulo=modulo)
        if erros:
            invalido = pasta / f"{etapa}.lote{i}.invalido.json"
            invalido.write_text(gerados.model_dump_json(indent=2), encoding="utf-8")
            raise PipelineError(
                f"Etapa '{etapa}' (lote {i}/{len(lotes)}) não passou na validação após "
                f"{max_tentativas} tentativas:\n- " + "\n- ".join(erros)
                + f"\nÚltima saída em {invalido}")
        esperados = {f.path for f in lote}
        gerados.files = [f for f in gerados.files if f.path in esperados]
        for caminho in gravar(raiz, gerados):
            manifesto.append({"path": caminho, "lote": i})
        if gerados.notes:
            manifesto.append({"lote": i, "notas": gerados.notes})
        metricas[etapa]["lotes"] = len(lotes)
    return manifesto


# ------------------------------------------------------------- conhecidos
def _conhecidos(raiz_lado: Path, planos: Iterable[Optional[ImplementationPlan]],
                lado: str, ctx: ProjectContext, **extra) -> validation.Conhecidos:
    caminhos = caminhos_em_disco(raiz_lado)
    for p in planos:
        if p:
            caminhos |= {f.path for f in getattr(p, f"{lado}_files")}
    return validation.Conhecidos(
        caminhos=caminhos,
        pacote_base=validation.convencoes(ctx).pacote_base,
        **extra)


# ------------------------------------------------------------- base
def garantir_base(
    contexto: ProjectContext,
    modulos: List[str],
    pasta: Path,
    codigo_dir: Path,
    agentes: dict,
    ate_lado: Optional[str],
    max_tentativas: int = MAX_TENTATIVAS,
    metricas: Optional[dict] = None,
) -> ImplementationPlan:
    """Planeja e implementa a base (o que ainda não existir em `pasta`).

    `ate_lado`: None = só planejar; "backend" ou "frontend" = implementar até ele.
    Artefatos: <pasta>/5_plano.json, 6_backend.json, 7_frontend.json.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    metricas = {} if metricas is None else metricas
    conv = validation.convencoes(contexto)
    rotas = layout.rotas_dos_modulos(modulos)
    front = layout.tem_frontend(contexto)

    arq_plano = pasta / "5_plano.json"
    if arq_plano.exists():
        plano = ImplementationPlan.model_validate_json(arq_plano.read_text(encoding="utf-8"))
    else:
        print("[base] Planejador — esqueleto compartilhado")
        proibidos = layout.prefixos_proibidos_base(contexto, conv.pacote_base, modulos)
        plano, erros = executar_agente(
            "planejador_base", agentes["planejador"],
            templates.msg_planejador_base(modulos, rotas, proibidos, front),
            lambda p: validation.validar_plano_base(p, contexto, modulos),
            metricas, max_tentativas, modulo="base")
        if erros:
            (pasta / "planejador_base.invalido.json").write_text(
                plano.model_dump_json(indent=2), encoding="utf-8")
            raise PipelineError("Plano da base não passou na validação:\n- "
                                + "\n- ".join(erros))
        arq_plano.write_text(plano.model_dump_json(indent=2), encoding="utf-8")

    if ate_lado is None:
        return plano
    lados = LADOS[: LADOS.index(ate_lado) + 1]
    for lado in lados:
        manifesto_arq = pasta / f"{6 + LADOS.index(lado)}_{lado}.json"
        arquivos = getattr(plano, f"{lado}_files")
        if manifesto_arq.exists() or not arquivos or (lado == "frontend" and not front):
            continue
        raiz = codigo_dir / lado
        print(f"[base] Implementador {lado}")
        manifesto = executar_lotes(
            f"implementador_{lado}_base", agentes[f"implementador_{lado}"], arquivos, raiz,
            lambda lote, deps, i, n, lado=lado: templates.msg_implementador_base(
                lado, modulos, rotas, plano, deps, lote, i, n),
            lambda raiz=raiz, lado=lado: _conhecidos(
                raiz, [plano], lado, contexto, rotas_modulos=set(rotas)),
            pasta, metricas, max_tentativas, "base")
        _salvar_json(manifesto_arq, manifesto)
    _salvar_json(pasta / "metricas.json", metricas)
    return plano


# ------------------------------------------------------------- módulo
def planejar_modulo(
    nome: str, contexto: ProjectContext, spec: FinalSpecification,
    modelo: ModuleDataModel, arq: ModuleArchitecture,
    plano_base: Optional[ImplementationPlan], numero_migracao: int,
    outros_modulos: str, agente, metricas: dict, max_tentativas: int,
):
    lay = layout.layout_modulo(contexto, nome, arq.java_package)
    ddl = gerar_ddl(modelo) if usa_sql(contexto) else ""
    msg = templates.msg_planejador(
        nome, spec, modelo, arq, lay, layout.tem_frontend(contexto),
        layout.migracao_modulo(nome, numero_migracao), ddl, plano_base, outros_modulos)
    return executar_agente(
        "planejador", agente, msg,
        lambda p: validation.validar_plano(p, nome, spec, modelo, arq, contexto, plano_base),
        metricas, max_tentativas, modulo=nome)


def implementar_backend(
    nome: str, contexto: ProjectContext, spec: FinalSpecification,
    modelo: ModuleDataModel, arq: ModuleArchitecture, plano: ImplementationPlan,
    plano_base: Optional[ImplementationPlan], numero_migracao: int,
    codigo_dir: Path, pasta: Path, agente, metricas: dict, max_tentativas: int,
) -> List[dict]:
    raiz = codigo_dir / "backend"
    lay = layout.layout_modulo(contexto, nome, arq.java_package)
    manifesto: List[dict] = []
    ddl = ""
    if usa_sql(contexto):
        ddl = gerar_ddl(modelo)
        migracao = layout.migracao_modulo(nome, numero_migracao)
        destino = _destino(raiz, migracao)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(ddl, encoding="utf-8")
        manifesto.append({"path": migracao, "lote": 0, "gerado_por": "ddl.py"})
    externas = interfaces_externas(raiz, arq, lay.backend_prefixos)
    tabelas = {e.table for e in modelo.entities}
    manifesto += executar_lotes(
        "implementador_backend", agente, plano.backend_files, raiz,
        lambda lote, deps, i, n: templates.msg_implementador_backend(
            nome, spec, arq, plano, plano_base, ddl, externas, deps, lote, i, n),
        lambda: _conhecidos(raiz, [plano, plano_base], "backend", contexto,
                            tabelas=tabelas),
        pasta, metricas, max_tentativas, nome)
    return manifesto


def implementar_frontend(
    nome: str, contexto: ProjectContext, spec: FinalSpecification,
    arq: ModuleArchitecture, plano: ImplementationPlan,
    plano_base: Optional[ImplementationPlan], endpoints_externos: Set[str],
    codigo_dir: Path, pasta: Path, agente, metricas: dict, max_tentativas: int,
) -> List[dict]:
    raiz = codigo_dir / "frontend"
    endpoints = {validation.normalizar_rota(e.path) for e in arq.endpoints}
    endpoints |= {validation.normalizar_rota(e) for e in endpoints_externos}
    return executar_lotes(
        "implementador_frontend", agente, plano.frontend_files, raiz,
        lambda lote, deps, i, n: templates.msg_implementador_frontend(
            nome, spec, arq, plano, plano_base, deps, lote, i, n),
        lambda: _conhecidos(raiz, [plano, plano_base], "frontend", contexto,
                            endpoints=endpoints),
        pasta, metricas, max_tentativas, nome)
