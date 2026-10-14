"""Pipeline de UM módulo:
   Requisitos -> Esclarecedor -> Modelador de dados -> Arquiteto de software
   -> Planejador -> Implementador backend -> Implementador frontend.

Todos os agentes recebem o PROJECT CONTEXT (saída do Arquiteto-chefe). Sem
contexto explícito, usa o arquivo de CONTEXTO_PADRAO (config.py).

Cada etapa:
  1. invoca o agente (saída estruturada Pydantic);
  2. valida em código (validation.py);
  3. se falhar, re-executa com os erros anexados (até MAX_TENTATIVAS);
  4. publica o artefato aprovado no ambiente compartilhado (ambiente.py),
     em saida/<modulo>/, antes de seguir.

As etapas de implementação estão em implementacao.py. O código gerado vai para
<codigo_dir>/backend e <codigo_dir>/frontend.
"""
import json
from pathlib import Path
from typing import Optional, Set

from langsmith import get_current_run_tree, traceable

import implementacao
import layout
import validation
from ambiente import ARTEFATOS, Ambiente
from config import CONTEXTO_PADRAO, MAX_TENTATIVAS, SAIDA_DIR
from execucao import PipelineError, executar_agente
from layout import slugify
from render import renderizar_relatorio
from schemas import ImplementationPlan, ProjectContext
from system_prompts import templates

ETAPAS = ("requisitos", "esclarecedor", "modelador_dados", "arquiteto_software",
          "planejador", "implementador_backend", "implementador_frontend")
ETAPAS_ESPECIFICACAO = ETAPAS[:4]

# artefato -> etapas que o consomem
CONSUMIDORES = {
    "analise": ("esclarecedor",),
    "especificacao": ETAPAS[2:],
    "modelo_dados": ("arquiteto_software", "planejador", "implementador_backend"),
    "arquitetura": ETAPAS[4:],
    "plano": ETAPAS[5:],
}

# compatibilidade: estes nomes eram definidos aqui
_executar = executar_agente
__all__ = ["ETAPAS", "PipelineError", "processar_modulo", "carregar_contexto",
           "slugify", "_executar"]


def carregar_contexto(caminho: str = CONTEXTO_PADRAO) -> ProjectContext:
    return ProjectContext.model_validate_json(Path(caminho).read_text(encoding="utf-8"))


def criar_agentes(contexto: ProjectContext) -> dict:
    from agents import (arquiteto_software, esclarecedor, implementador_backend,
                        implementador_frontend, modelador_de_dados, planejador,
                        requisitos)
    return {
        "requisitos": requisitos.get_agente(contexto),
        "esclarecedor": esclarecedor.get_agente(contexto),
        "modelador_dados": modelador_de_dados.get_agente(contexto),
        "arquiteto_software": arquiteto_software.get_agente(contexto),
        "planejador": planejador.get_agente(contexto),
        "implementador_backend": implementador_backend.get_agente(contexto),
        "implementador_frontend": implementador_frontend.get_agente(contexto),
    }


def _caminhos(manifesto) -> list:
    return [m["path"] for m in manifesto if "path" in m]


def lado_final(ate: str) -> Optional[str]:
    """Até que lado a base precisa estar implementada para rodar até `ate`."""
    if ate == "implementador_frontend":
        return "frontend"
    if ate == "implementador_backend":
        return "backend"
    return None


@traceable(name="pipeline_modulo", run_type="chain")
def processar_modulo(
    nome: str,
    descricao: str,
    outros_modulos: Optional[str] = None,
    ate: str = ETAPAS[-1],
    agentes: Optional[dict] = None,
    saida_dir: str = SAIDA_DIR,
    max_tentativas: int = MAX_TENTATIVAS,
    contexto: Optional[ProjectContext] = None,
    de: str = ETAPAS[0],
    plano_base: Optional[ImplementationPlan] = None,
    codigo_dir: Optional[str] = None,
    numero_migracao: int = 1,
    endpoints_externos: Optional[Set[str]] = None,
    ambiente: Optional[Ambiente] = None,
) -> dict:
    """Roda o pipeline de um módulo e devolve os artefatos produzidos.

    - `outros_modulos=None` gera o resumo dos outros módulos a partir do contexto.
    - `de`: retoma a partir desta etapa, lendo de saida/<modulo>/ os artefatos
      das etapas anteriores (evita refazer chamadas já pagas).
    - `plano_base`: base compartilhada já pronta (modo projeto). Sem ela, a base
      é gerada em saida/<modulo>/base/ quando alguma etapa de implementação roda.
    - `codigo_dir`: onde gravar o código (padrão: saida/<modulo>/codigo).
    - `ambiente`: ambiente compartilhado do projeto. Sem ele, cria um só para
      este módulo (saida/<modulo>/, com base e código dentro dessa pasta).
    - `agentes` permite injetar agentes falsos em testes.
    """
    for nome_param, valor in (("ate", ate), ("de", de)):
        if valor not in ETAPAS:
            raise ValueError(f"'{nome_param}' deve ser um de {ETAPAS}")
    if ETAPAS.index(de) > ETAPAS.index(ate):
        raise ValueError("'de' não pode vir depois de 'ate'")
    if contexto is None:
        contexto = carregar_contexto()
    if outros_modulos is None:
        outros_modulos = templates.resumo_outros_modulos(contexto, nome)
    conv = validation.convencoes(contexto)

    rt = get_current_run_tree()
    if rt:
        rt.add_metadata({"modulo": nome, "ate": ate, "de": de})
        rt.add_tags([f"modulo:{slugify(nome)}"])

    if agentes is None:
        agentes = criar_agentes(contexto)

    if ambiente is None:
        pasta = Path(saida_dir) / slugify(nome)
        ambiente = Ambiente(Path(saida_dir), contexto,
                            codigo=Path(codigo_dir) if codigo_dir else pasta / "codigo",
                            base=pasta / "base", registro=pasta / "ambiente.jsonl")
    amb = ambiente
    pasta = amb.pasta_modulo(nome)
    codigo = amb.codigo
    metricas: dict = {}
    if de != ETAPAS[0] and (pasta / "metricas.json").exists():
        metricas = json.loads((pasta / "metricas.json").read_text())
    artefatos: dict = {}

    def roda(etapa: str) -> bool:
        return ETAPAS.index(de) <= ETAPAS.index(etapa) <= ETAPAS.index(ate)

    # Artefatos das etapas anteriores a `de` são lidos do disco
    for etapa in ETAPAS[:ETAPAS.index(de)]:
        if etapa not in ARTEFATOS:
            continue
        chave = ARTEFATOS[etapa][0]
        obj = amb.artefato(nome, chave)
        if obj is not None:
            artefatos[chave] = obj
        elif any(roda(e) for e in CONSUMIDORES[chave]):
            raise PipelineError(
                f"Para começar em '{de}' é preciso {amb.caminho_artefato(nome, chave)} "
                f"(etapa '{etapa}').")

    def falhar(etapa, erros, obj):
        (pasta / f"{etapa}.invalido.json").write_text(
            obj.model_dump_json(indent=2), encoding="utf-8")
        (pasta / "metricas.json").write_text(json.dumps(metricas, indent=2))
        raise PipelineError(
            f"Etapa '{etapa}' não passou na validação após {max_tentativas} "
            f"tentativas:\n- " + "\n- ".join(erros)
            + f"\nÚltima saída em {pasta}/{etapa}.invalido.json"
        )

    def etapa_estruturada(etapa, mensagem, validar):
        print(f"[{ETAPAS.index(etapa) + 1}/{ETAPAS.index(ate) + 1}] {etapa} — {nome}")
        obj, erros = executar_agente(etapa, agentes[etapa], mensagem, validar,
                                     metricas, max_tentativas, modulo=nome)
        if erros:
            falhar(etapa, erros, obj)
        amb.publicar(nome, etapa, obj)
        artefatos[ARTEFATOS[etapa][0]] = obj
        return obj

    # 1) Requisitos -------------------------------------------------------
    if roda("requisitos"):
        etapa_estruturada(
            "requisitos", templates.msg_requisitos(nome, descricao, outros_modulos),
            validation.validar_analise)

    # 2) Esclarecedor -----------------------------------------------------
    if roda("esclarecedor"):
        analise = artefatos["analise"]
        etapa_estruturada(
            "esclarecedor",
            templates.msg_esclarecedor(nome, descricao, analise, outros_modulos),
            lambda e: validation.validar_especificacao(e, analise))

    # 3) Modelador de dados ---------------------------------------------
    if roda("modelador_dados"):
        spec = artefatos["especificacao"]
        etapa_estruturada(
            "modelador_dados", templates.msg_modelador_dados(nome, spec, outros_modulos),
            lambda m: validation.validar_modelo_dados(m, spec, conv))

    # 4) Arquiteto de software ------------------------------------------
    if roda("arquiteto_software"):
        spec, modelo = artefatos["especificacao"], artefatos["modelo_dados"]
        etapa_estruturada(
            "arquiteto_software",
            templates.msg_arquiteto_software(nome, spec, modelo, outros_modulos),
            lambda a: validation.validar_arquitetura(a, spec, modelo, conv))

    try:
        return _etapas_de_implementacao(
            nome, contexto, artefatos, agentes, amb, pasta, codigo, metricas, ate, roda,
            falhar, plano_base, numero_migracao, outros_modulos, endpoints_externos,
            max_tentativas)
    finally:
        # grava métricas mesmo se uma etapa de implementação falhar
        (pasta / "metricas.json").write_text(json.dumps(metricas, indent=2))


def _etapas_de_implementacao(nome, contexto, artefatos, agentes, amb, pasta, codigo,
                             metricas, ate, roda, falhar, plano_base,
                             numero_migracao, outros_modulos, endpoints_externos,
                             max_tentativas) -> dict:
    # Base compartilhada (esqueleto) — só quando há implementação a fazer
    if any(roda(e) for e in ETAPAS[4:]) and plano_base is None:
        arq = artefatos.get("arquitetura")
        plano_base = implementacao.garantir_base(
            contexto, [nome], amb.base, codigo, agentes, lado_final(ate),
            max_tentativas, arquiteturas={nome: arq} if arq else None,
            ambiente=amb)

    # 5) Planejador ------------------------------------------------------
    if roda("planejador"):
        print(f"[5/{ETAPAS.index(ate) + 1}] planejador — {nome}")
        plano, erros = implementacao.planejar_modulo(
            nome, contexto, artefatos["especificacao"], artefatos["modelo_dados"],
            artefatos["arquitetura"], plano_base, numero_migracao, outros_modulos,
            agentes["planejador"], metricas, max_tentativas)
        if erros:
            falhar("planejador", erros, plano)
        amb.publicar(nome, "planejador", plano)
        artefatos["plano"] = plano

    # 6) Implementador backend -------------------------------------------
    if roda("implementador_backend"):
        print(f"[6/{ETAPAS.index(ate) + 1}] implementador_backend — {nome}")
        manifesto = implementacao.implementar_backend(
            nome, contexto, artefatos["especificacao"], artefatos["modelo_dados"],
            artefatos["arquitetura"], artefatos["plano"], plano_base, numero_migracao,
            codigo, pasta, agentes["implementador_backend"], metricas, max_tentativas)
        (pasta / "6_backend.json").write_text(json.dumps(manifesto, indent=2))
        amb.registrar("implementador_backend", "codigo", nome, _caminhos(manifesto))
        artefatos["arquivos_backend"] = manifesto

    # 7) Implementador frontend ------------------------------------------
    if roda("implementador_frontend"):
        plano = artefatos["plano"]
        if layout.tem_frontend(contexto) and plano.frontend_files:
            print(f"[7/{ETAPAS.index(ate) + 1}] implementador_frontend — {nome}")
            manifesto = implementacao.implementar_frontend(
                nome, contexto, artefatos["especificacao"], artefatos["arquitetura"],
                plano, plano_base, endpoints_externos or set(), codigo, pasta,
                agentes["implementador_frontend"], metricas, max_tentativas)
            (pasta / "7_frontend.json").write_text(json.dumps(manifesto, indent=2))
            amb.registrar("implementador_frontend", "codigo", nome, _caminhos(manifesto))
            artefatos["arquivos_frontend"] = manifesto
        else:
            print(f"[7/7] implementador_frontend — sem frontend para {nome}, pulado")

    (pasta / "relatorio.md").write_text(
        renderizar_relatorio(nome, artefatos), encoding="utf-8")
    (pasta / "metricas.json").write_text(json.dumps(metricas, indent=2))
    artefatos["pasta"] = str(pasta)
    artefatos["codigo"] = str(codigo)
    artefatos["metricas"] = metricas
    return artefatos
