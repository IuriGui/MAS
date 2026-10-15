"""Pipeline do PROJETO inteiro:
   Arquiteto-chefe (uma vez) -> base compartilhada (esqueleto de código, uma vez)
   -> pipeline de cada módulo na ordem de build_order.

Cada módulo recebe o PROJECT CONTEXT e um resumo dos outros módulos que inclui
os serviços públicos e eventos já publicados no ambiente compartilhado
(ambiente.py) pelos módulos anteriores. O código de todos os módulos vai para
saida/<projeto>/codigo/{backend,frontend}.
"""
import json
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from langsmith import traceable

import implementacao
import validation
from ambiente import Ambiente
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
    Ambiente(pasta).publicar_contexto(contexto)
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
    - `apenas`: processa só estes módulos (os demais seguem como contexto). Com
      implementação, o subconjunto precisa incluir os módulos de que ele depende,
      e a base compartilhada é planejada só para ele.
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

    amb = Ambiente(pasta, contexto)
    codigo = amb.codigo
    plano_base = None
    # Arquiteturas de execuções anteriores (se houver) ajudam a base (ex.: login)
    arquiteturas = amb.arquiteturas()
    implementa = ETAPAS.index(ate) >= ETAPAS.index("planejador")
    escolhidos = [n for n in contexto.build_order
                  if not filtro or n.strip().lower() in filtro]
    if implementa and filtro:
        faltando = dependencias_faltando(contexto, escolhidos, arquiteturas)
        if faltando:
            raise PipelineError(
                "Para implementar só " + ", ".join(escolhidos) + ", inclua também "
                "os módulos de que eles dependem: " + "; ".join(
                    f"{m} usa {', '.join(d)}" for m, d in faltando.items()))
    if implementa:
        agentes_base = fabrica_agentes(contexto) if fabrica_agentes else None
        if agentes_base is None:
            from pipeline import criar_agentes
            agentes_base = criar_agentes(contexto)
        plano_base = implementacao.garantir_base(
            contexto, escolhidos, amb.base, codigo,
            agentes_base, lado_final(ate), max_tentativas,
            arquiteturas=arquiteturas or None, ambiente=amb)

    status: Dict[str, dict] = {}
    for numero, nome in enumerate(contexto.build_order, 1):
        if filtro and nome.strip().lower() not in filtro:
            continue
        descricao = _descricao_modulo(nome, modulos_dir, declarados, entradas)
        print(f"\n=== Módulo {nome} ===")
        try:
            res = processar_modulo(
                nome, descricao,
                outros_modulos=templates.resumo_outros_modulos(
                    contexto, nome, amb.arquiteturas()),
                ate=ate, de=de,
                agentes=fabrica_agentes(contexto) if fabrica_agentes else None,
                saida_dir=str(pasta), max_tentativas=max_tentativas,
                contexto=contexto, plano_base=plano_base,
                codigo_dir=str(codigo), numero_migracao=numero,
                endpoints_externos=amb.endpoints_de_outros(nome), ambiente=amb)
        except PipelineError as e:
            print(f"  FALHA em {nome}: {e}")
            status[nome] = {"status": "falhou", "erro": str(e)}
            continue
        spec = res.get("especificacao")
        status[nome] = {
            "status": "ok",
            "pasta": res["pasta"],
            "review_alerts": spec.review_alerts if spec else [],
            "pendencias": res.get("pendencias", []),
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


def dependencias_faltando(contexto: ProjectContext, escolhidos: List[str],
                          arquiteturas: dict) -> dict:
    """{módulo escolhido: dependências fora do conjunto}, pelo module_map e pelas
    dependências que a arquitetura de cada módulo declara."""
    nomes = {n.strip().lower() for n in escolhidos}
    mapa = {m.name.strip().lower(): m for m in contexto.module_map}
    arqs = {n.strip().lower(): a for n, a in arquiteturas.items()}
    faltando = {}
    for nome in escolhidos:
        chave = nome.strip().lower()
        deps = set(mapa[chave].depends_on) if chave in mapa else set()
        if chave in arqs:
            deps |= {d.module for d in arqs[chave].module_dependencies}
        fora = sorted(d for d in deps if d.strip().lower() not in nomes)
        if fora:
            faltando[nome] = fora
    return faltando


def _descricao_modulo(nome, modulos_dir, declarados, entradas) -> str:
    if modulos_dir:
        arq = Path(modulos_dir) / f"{slugify(nome)}.md"
        if arq.exists():
            return arq.read_text(encoding="utf-8")
    if nome.strip().lower() in declarados:
        return f"{nome}: {declarados[nome.strip().lower()]}"
    return f"{nome}: {entradas[nome].responsibility}"

