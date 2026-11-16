"""Etapas de implementação: Planejador -> Implementador backend -> frontend.

- A BASE (esqueleto compartilhado) é planejada e implementada uma vez por
  projeto; os módulos se encaixam nela.
- Cada Implementador gera o plano em LOTES (IMPL_LOTE arquivos por chamada),
  em ordem topológica de depends_on; cada lote recebe o conteúdo já gerado dos
  arquivos de que depende.
- Cada lote é validado em código (validacao.validar_arquivos) e só então
  gravado em disco, em <codigo>/backend e <codigo>/frontend
  (codigo/arquivos.py, que também repara caminhos trocados).
- A migration do módulo é gerada sem LLM (codigo/ddl.py).
- No modo sandbox (config.SANDBOX), o Implementador escreve o lote direto no
  workspace do container e compila/testa lá; o pipeline colhe o lote do disco
  (codigo/coleta.py), repara caminhos, descarta mudanças fora do lote e valida.
"""
import json
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from mas import validacao
from mas.agentes import mensagens
from mas.codigo import layout
from mas.codigo.arquivos import (caminho_seguro, caminhos_em_disco, gravar,
                                 reparar_caminhos)
from mas.codigo.assinaturas import assinaturas
from mas.codigo.coleta import ColetaSandbox
from mas.codigo.ddl import gerar_ddl, usa_sql
from mas.config import (IMPL_LIMITE_CONTEXTO, IMPL_LOTE, LIMITE_PASSOS_AGENTE,
                        LIMITE_PASSOS_SANDBOX, MAX_TENTATIVAS)
from mas.pipeline.execucao import PipelineError, executar_agente
from mas.schemas import (FinalSpecification, ImplementationPlan, ModuleArchitecture,
                         ModuleDataModel, PlannedFile, ProjectContext)

LADOS = ("backend", "frontend")


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
    """Conteúdo dos arquivos (já gravados) de que o lote depende.

    Até `limite` caracteres vão inteiros; os seguintes vão só com as
    assinaturas públicas (nunca omitidos: sem eles o modelo inventa APIs).
    """
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
                bloco = (f"=== {d} === (só assinaturas públicas: limite de contexto)\n"
                         f"{assinaturas(d, texto)}")
            partes.append(bloco)
            total += len(bloco)
    return "\n\n".join(partes)


def api_da_base(raiz_lado: Path, plano_base: Optional[ImplementationPlan], lado: str) -> str:
    """Assinaturas públicas de todos os arquivos da base já gerados neste lado.

    Os módulos usam construtores, métodos, props e tipos da base; só caminho e
    `exports` não bastam (na 2ª execução real o modelo errou o construtor de
    BusinessException, as props de ProtectedRoute e os campos de PaginatedResponse).
    """
    if not plano_base:
        return ""
    partes = []
    for f in getattr(plano_base, f"{lado}_files"):
        arquivo = raiz_lado / f.path
        if arquivo.is_file():
            resumo = assinaturas(f.path, arquivo.read_text(encoding="utf-8"))
            if resumo:
                partes.append(f"=== {f.path} ===\n{resumo}")
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


def arquivo_progresso(pasta: Path, etapa: str) -> Path:
    return pasta / f"{etapa}.progresso.json"


def executar_lotes(
    etapa: str,
    agente,
    arquivos: List[PlannedFile],
    raiz: Path,
    montar_mensagem: Callable[[List[PlannedFile], str, int, int], str],
    conhecidos: Callable[[], validacao.Conhecidos],
    pasta: Path,
    metricas: dict,
    max_tentativas: int,
    modulo: str,
) -> List[dict]:
    """Gera os arquivos lote a lote, validando e gravando cada lote.

    Retomada: cada lote aprovado é registrado em <etapa>.progresso.json. Numa
    nova execução, arquivos já registrados (e ainda presentes em disco) não são
    gerados de novo. Ao terminar a etapa, o arquivo de progresso é apagado.

    Agente com `sandbox` (agentes/fabrica.py, AgenteSandbox): o lote é colhido
    do disco por ColetaSandbox em vez da saída estruturada.
    """
    sandbox = getattr(agente, "sandbox", None)
    progresso = arquivo_progresso(pasta, etapa)
    manifesto: List[dict] = []
    if progresso.exists():
        manifesto = json.loads(progresso.read_text(encoding="utf-8"))
    feitos = {m["path"] for m in manifesto if "path" in m and (raiz / m["path"]).is_file()}
    pendentes = [f for f in arquivos if f.path not in feitos]
    if feitos:
        print(f"  [{etapa}] retomando: {len(feitos)} arquivo(s) já gerado(s), "
              f"{len(pendentes)} pendente(s)")

    lotes = montar_lotes(pendentes)
    for i, lote in enumerate(lotes, 1):
        print(f"  [{etapa}] lote {i}/{len(lotes)}: {len(lote)} arquivo(s)")
        msg = montar_mensagem(lote, conteudo_dependencias(raiz, lote), i, len(lotes))
        k = conhecidos()
        coleta = None
        if sandbox is not None:
            coleta = ColetaSandbox(sandbox.host_dir, raiz, lote)
            msg += mensagens.msg_sandbox(lote, sandbox.caminho_no_container(raiz),
                                         getattr(agente, "ambiente", ""))
        gerados, erros = executar_agente(
            etapa, agente, msg, lambda g: validacao.validar_arquivos(g, lote, k),
            metricas, max_tentativas, modulo=modulo,
            normalizar=None if coleta else (lambda g: reparar_caminhos(g, lote)),
            extrair=coleta,
            limite_passos=LIMITE_PASSOS_SANDBOX if coleta else LIMITE_PASSOS_AGENTE)
        if erros:
            if coleta:
                coleta.desfazer()
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
        _salvar_json(progresso, manifesto)
        metricas[etapa]["lotes"] = metricas[etapa].get("lotes", 0) + 1
    progresso.unlink(missing_ok=True)
    return manifesto


# ------------------------------------------------------------- conhecidos
def _conhecidos(raiz_lado: Path, planos: Iterable[Optional[ImplementationPlan]],
                lado: str, ctx: ProjectContext, **extra) -> validacao.Conhecidos:
    caminhos = caminhos_em_disco(raiz_lado)
    for p in planos:
        if p:
            caminhos |= {f.path for f in getattr(p, f"{lado}_files")}
    return validacao.Conhecidos(
        caminhos=caminhos,
        pacote_base=validacao.convencoes(ctx).pacote_base,
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
    arquiteturas: Optional[Dict[str, ModuleArchitecture]] = None,
) -> ImplementationPlan:
    """Planeja e implementa a base (o que ainda não existir em `pasta`).

    `ate_lado`: None = só planejar; "backend" ou "frontend" = implementar até ele.
    `arquiteturas`: arquiteturas de módulo já conhecidas. Seus endpoints vão na
    mensagem (ex.: login) e na validação; sem nenhuma, a base não tem as URLs
    da API checadas.
    Artefatos: <pasta>/5_plano.json, 6_backend.json, 7_frontend.json.
    """
    pasta.mkdir(parents=True, exist_ok=True)
    metricas = {} if metricas is None else metricas
    conv = validacao.convencoes(contexto)
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
            mensagens.msg_planejador_base(modulos, rotas, proibidos, front),
            lambda p: validacao.validar_plano_base(p, contexto, modulos),
            metricas, max_tentativas, modulo="base")
        if erros:
            (pasta / "planejador_base.invalido.json").write_text(
                plano.model_dump_json(indent=2), encoding="utf-8")
            _salvar_json(pasta / "metricas.json", metricas)
            raise PipelineError("Plano da base não passou na validação:\n- "
                                + "\n- ".join(erros))
        arq_plano.write_text(plano.model_dump_json(indent=2), encoding="utf-8")

    if ate_lado is None:
        _salvar_json(pasta / "metricas.json", metricas)
        return plano
    try:
        _implementar_base(contexto, modulos, pasta, codigo_dir, agentes, ate_lado,
                          max_tentativas, metricas, plano, rotas, front,
                          arquiteturas or {})
    finally:
        _salvar_json(pasta / "metricas.json", metricas)  # mesmo se falhar
    return plano


def _implementar_base(contexto, modulos, pasta, codigo_dir, agentes, ate_lado,
                      max_tentativas, metricas, plano, rotas, front,
                      arquiteturas: Dict[str, ModuleArchitecture]) -> None:
    lados = LADOS[: LADOS.index(ate_lado) + 1]
    endpoints = [e for a in arquiteturas.values() for e in a.endpoints]
    conhecidos_api = ({validacao.normalizar_rota(e.path) for e in endpoints}
                      if endpoints else None)
    for lado in lados:
        manifesto_arq = pasta / f"{6 + LADOS.index(lado)}_{lado}.json"
        arquivos = getattr(plano, f"{lado}_files")
        if manifesto_arq.exists() or not arquivos or (lado == "frontend" and not front):
            continue
        raiz = codigo_dir / lado
        print(f"[base] Implementador {lado}")
        manifesto = executar_lotes(
            f"implementador_{lado}_base", agentes[f"implementador_{lado}"], arquivos, raiz,
            lambda lote, deps, i, n, lado=lado: mensagens.msg_implementador_base(
                lado, modulos, rotas, plano, deps, lote, i, n, endpoints),
            lambda raiz=raiz, lado=lado: _conhecidos(
                raiz, [plano], lado, contexto, rotas_modulos=set(rotas),
                endpoints=conhecidos_api),
            pasta, metricas, max_tentativas, "base")
        _salvar_json(manifesto_arq, manifesto)


# ------------------------------------------------------------- módulo
def planejar_modulo(
    nome: str, contexto: ProjectContext, spec: FinalSpecification,
    modelo: ModuleDataModel, arq: ModuleArchitecture,
    plano_base: Optional[ImplementationPlan], numero_migracao: int,
    outros_modulos: str, agente, metricas: dict, max_tentativas: int,
):
    lay = layout.layout_modulo(contexto, nome, arq.java_package)
    ddl = gerar_ddl(modelo) if usa_sql(contexto) else ""
    msg = mensagens.msg_planejador(
        nome, spec, modelo, arq, lay, layout.tem_frontend(contexto),
        layout.migracao_modulo(nome, numero_migracao), ddl, plano_base, outros_modulos)
    return executar_agente(
        "planejador", agente, msg,
        lambda p: validacao.validar_plano(p, nome, spec, modelo, arq, contexto, plano_base),
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
        destino = caminho_seguro(raiz, migracao)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(ddl, encoding="utf-8")
        manifesto.append({"path": migracao, "lote": 0, "gerado_por": "ddl.py"})
    externas = interfaces_externas(raiz, arq, lay.backend_prefixos)
    api_base = api_da_base(raiz, plano_base, "backend")
    tabelas = {e.table for e in modelo.entities}
    manifesto += executar_lotes(
        "implementador_backend", agente, plano.backend_files, raiz,
        lambda lote, deps, i, n: mensagens.msg_implementador_backend(
            nome, spec, arq, plano, plano_base, ddl, externas, deps, lote, i, n,
            api_base),
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
    endpoints = {validacao.normalizar_rota(e.path) for e in arq.endpoints}
    endpoints |= {validacao.normalizar_rota(e) for e in endpoints_externos}
    api_base = api_da_base(raiz, plano_base, "frontend")
    return executar_lotes(
        "implementador_frontend", agente, plano.frontend_files, raiz,
        lambda lote, deps, i, n: mensagens.msg_implementador_frontend(
            nome, spec, arq, plano, plano_base, deps, lote, i, n, api_base),
        lambda: _conhecidos(raiz, [plano, plano_base], "frontend", contexto,
                            endpoints=endpoints),
        pasta, metricas, max_tentativas, nome)
