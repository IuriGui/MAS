"""Pipeline do PROJETO inteiro:
   Arquiteto-chefe (uma vez) -> base compartilhada (esqueleto de código, uma vez)
   -> pipeline de cada módulo na ordem de build_order.

Cada módulo recebe o PROJECT CONTEXT e um resumo dos outros módulos que inclui
os serviços públicos e eventos já projetados pelos módulos anteriores. O código
de todos os módulos vai para saida/<projeto>/codigo/{backend,frontend}.
"""
import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from langsmith import traceable

import implementacao
import validation
from config import MAX_TENTATIVAS, SAIDA_DIR, VALIDAR
from pipeline import (ETAPAS, PipelineError, _executar, carregar_contexto,
                      lado_final, processar_modulo, slugify)
from render import render_contexto
from schemas import ProjectContext
from system_prompts import templates

_CABECALHO_MODULOS = re.compile(r"^\s*#*\s*(MODULES|MÓDULOS|MODULOS)\s*:?\s*$", re.I)
_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*])\s*([^:]+?)\s*:\s*(.*)$")


def ler_modulos(descricao_sistema: str) -> List[Tuple[str, str]]:
    """Extrai (nome, responsabilidade) da seção MODULES da descrição do sistema.

    Formato esperado (itens numerados ou com marcador; linhas indentadas
    continuam o item anterior):
        MODULES
        1. Users: user accounts, authentication...
        2. Customers: registry of customers,
           contacts and addresses.
    """
    modulos: List[List[str]] = []
    dentro = False
    for linha in descricao_sistema.splitlines():
        if not dentro:
            dentro = bool(_CABECALHO_MODULOS.match(linha))
            continue
        if not linha.strip():
            continue
        item = _ITEM.match(linha)
        if item:
            modulos.append([item.group(1).strip(), item.group(2).strip()])
        elif linha[0].isspace() and modulos:
            modulos[-1][1] += " " + linha.strip()
        else:
            break  # próxima seção (ex.: CONSTRAINTS)
    return [(n, d) for n, d in modulos]


def _salvar_json(caminho: Path, dados) -> None:
    caminho.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")


@traceable(name="arquiteto_chefe", run_type="chain")
def gerar_contexto(
    nome_projeto: str,
    descricao_sistema: str,
    pasta: Path,
    agente=None,
    max_tentativas: int = MAX_TENTATIVAS,
) -> ProjectContext:
    """Roda o Arquiteto-chefe, valida e grava 0_contexto.json / contexto.md."""
    modulos = ler_modulos(descricao_sistema)
    if not modulos:
        raise PipelineError(
            "Nenhum módulo encontrado na descrição do sistema. Use uma seção "
            "'MODULES' com itens no formato '1. Nome: responsabilidade'.")
    if agente is None:
        from agents import arquiteto_chefe
        agente = arquiteto_chefe.get_agente()

    pasta.mkdir(parents=True, exist_ok=True)
    metricas: dict = {}
    nomes = [n for n, _ in modulos]
    print(f"[0] Arquiteto-chefe — {len(modulos)} módulo(s) declarado(s)")
    contexto, erros = _executar(
        "arquiteto_chefe", agente,
        templates.msg_arquiteto_chefe(descricao_sistema, modulos),
        lambda c: validation.validar_contexto(c, nomes),
        metricas, max_tentativas, modulo=nome_projeto)
    _salvar_json(pasta / "metricas_contexto.json", metricas)
    if erros:
        (pasta / "arquiteto_chefe.invalido.json").write_text(
            contexto.model_dump_json(indent=2), encoding="utf-8")
        raise PipelineError(
            f"Arquiteto-chefe não passou na validação após {max_tentativas} "
            "tentativas:\n- " + "\n- ".join(erros))
    (pasta / "0_contexto.json").write_text(contexto.model_dump_json(indent=2), encoding="utf-8")
    (pasta / "contexto.md").write_text(render_contexto(nome_projeto, contexto), encoding="utf-8")
    return contexto


@traceable(name="pipeline_projeto", run_type="chain")
def processar_projeto(
    nome_projeto: str,
    descricao_sistema: str,
    modulos_dir: Optional[str] = None,
    contexto: Optional[ProjectContext] = None,
    apenas: Optional[List[str]] = None,
    ate: str = ETAPAS[-1],
    de: str = ETAPAS[0],
    saida_dir: str = SAIDA_DIR,
    max_tentativas: int = MAX_TENTATIVAS,
    agente_chefe=None,
    fabrica_agentes: Optional[Callable[[ProjectContext], dict]] = None,
) -> dict:
    """Roda o projeto e devolve {'pasta', 'contexto', 'modulos': {nome: status}}.

    - `contexto`: reaproveita um contexto já gerado (pula o Arquiteto-chefe).
    - `modulos_dir`: pasta com descrições detalhadas, uma por módulo, no arquivo
      <slug do nome>.md (ex.: users.md). Sem arquivo, usa a responsabilidade da
      seção MODULES (ou a do module_map, para módulos de plataforma).
    - `apenas`: processa só estes módulos (os demais seguem como contexto).
    - `de`: retoma cada módulo a partir desta etapa (artefatos lidos do disco).
    - Uma falha num módulo não interrompe os seguintes; fica registrada.
    - `agente_chefe` / `fabrica_agentes` permitem injetar agentes falsos em testes.
    """
    pasta = Path(saida_dir) / slugify(nome_projeto)
    pasta.mkdir(parents=True, exist_ok=True)

    if contexto is None and (pasta / "0_contexto.json").exists() and de != ETAPAS[0]:
        contexto = carregar_contexto(str(pasta / "0_contexto.json"))
    if contexto is None:
        contexto = gerar_contexto(nome_projeto, descricao_sistema, pasta,
                                  agente_chefe, max_tentativas)
    elif VALIDAR:
        erros = validation.validar_contexto(
            contexto, [n for n, _ in ler_modulos(descricao_sistema)])
        if erros:
            raise PipelineError("Contexto informado é inválido:\n- " + "\n- ".join(erros))

    declarados = {n.strip().lower(): d for n, d in ler_modulos(descricao_sistema)}
    entradas = {m.name: m for m in contexto.module_map}
    filtro = {n.strip().lower() for n in apenas} if apenas else None
    if filtro:
        desconhecidos = filtro - {n.lower() for n in contexto.build_order}
        if desconhecidos:
            raise PipelineError(f"Módulos não existem no contexto: {sorted(desconhecidos)}")

    codigo = pasta / "codigo"
    plano_base = None
    implementa = ETAPAS.index(ate) >= ETAPAS.index("planejador")
    if implementa:
        agentes_base = fabrica_agentes(contexto) if fabrica_agentes else None
        if agentes_base is None:
            from pipeline import criar_agentes
            agentes_base = criar_agentes(contexto)
        plano_base = implementacao.garantir_base(
            contexto, list(contexto.build_order), pasta / "base", codigo,
            agentes_base, lado_final(ate), max_tentativas)

    arquiteturas: Dict[str, object] = _arquiteturas_existentes(pasta, contexto)
    status: Dict[str, dict] = {}
    for numero, nome in enumerate(contexto.build_order, 1):
        if filtro and nome.strip().lower() not in filtro:
            continue
        descricao = _descricao_modulo(nome, modulos_dir, declarados, entradas)
        print(f"\n=== Módulo {nome} ===")
        externos = {e.path for n, a in arquiteturas.items() if n != nome
                    for e in a.endpoints}
        try:
            res = processar_modulo(
                nome, descricao,
                outros_modulos=templates.resumo_outros_modulos(contexto, nome, arquiteturas),
                ate=ate, de=de,
                agentes=fabrica_agentes(contexto) if fabrica_agentes else None,
                saida_dir=str(pasta), max_tentativas=max_tentativas,
                contexto=contexto, plano_base=plano_base,
                codigo_dir=str(codigo), numero_migracao=numero,
                endpoints_externos=externos)
        except PipelineError as e:
            print(f"  FALHA em {nome}: {e}")
            status[nome] = {"status": "falhou", "erro": str(e)}
            continue
        if "arquitetura" in res:
            arquiteturas[nome] = res["arquitetura"]
        spec = res.get("especificacao")
        status[nome] = {
            "status": "ok",
            "pasta": res["pasta"],
            "review_alerts": spec.review_alerts if spec else [],
            "metricas": res["metricas"],
        }

    _salvar_json(pasta / "projeto.json", {
        "projeto": nome_projeto,
        "ate": ate,
        "de": de,
        "codigo": str(codigo) if implementa else None,
        "build_order": contexto.build_order,
        "modulos": status,
    })
    return {"pasta": str(pasta), "contexto": contexto, "modulos": status}


def _arquiteturas_existentes(pasta: Path, contexto: ProjectContext) -> Dict[str, object]:
    """Arquiteturas já geradas em execuções anteriores (úteis com --apenas/--de)."""
    from schemas import ModuleArchitecture
    achadas = {}
    for nome in contexto.build_order:
        arq = pasta / slugify(nome) / "4_arquitetura.json"
        if arq.exists():
            achadas[nome] = ModuleArchitecture.model_validate_json(
                arq.read_text(encoding="utf-8"))
    return achadas


def _descricao_modulo(nome, modulos_dir, declarados, entradas) -> str:
    if modulos_dir:
        arq = Path(modulos_dir) / f"{slugify(nome)}.md"
        if arq.exists():
            return arq.read_text(encoding="utf-8")
    if nome.strip().lower() in declarados:
        return f"{nome}: {declarados[nome.strip().lower()]}"
    return f"{nome}: {entradas[nome].responsibility}"

