"""Etapa Build: autocorreção, pedido de mudança ao dono de outro arquivo, recusa,
validação das edições e parada do laço.

O compilador é falso (marcadores no texto viram erros) e os agentes falsos chamam
as tools reais (request_change) dentro da sessão que o orquestrador abre.
"""
import json
import re
from pathlib import Path

import pytest

import build
import correcao
import pipeline
from ambiente import Ambiente
from build import Erro, Resultado
from correcao import EtapaBuild, aplicar_edicoes
from pipeline import processar_modulo
from schemas import BuildFix, CodeEdit
from tests.test_comunicacao import AgenteRoteiro
from tests.test_pipeline import AgenteFalso, agentes
from tests.test_projeto import contexto

JAVA = "src/main/java/br/x"
SERVICO = f"{JAVA}/catalog/Servico.java"
CACHE = f"{JAVA}/catalog/CacheConfig.java"
USUARIO = f"{JAVA}/users/Usuario.java"


def compilador(codigo, lados=("backend", "frontend"), alteracoes=None, **kw):
    """Marcadores: SINTAXE (bloqueante), ERRO:x, CHAMA:x sem DEF:x em algum arquivo,
    caffeine sem starter-cache no pom.xml. Como o javac, um erro bloqueante
    esconde os demais."""
    saida = []
    for lado in lados:
        raiz = Path(codigo) / lado
        if not raiz.exists():
            continue
        arquivos = {p.relative_to(raiz).as_posix(): p.read_text()
                    for p in raiz.rglob("*") if p.is_file()}
        arquivos.update((alteracoes or {}).get(lado, {}))
        tudo = "\n".join(arquivos.values())
        erros = []
        for caminho, texto in sorted(arquivos.items()):
            for n, linha in enumerate(texto.splitlines(), 1):
                if "SINTAXE" in linha:
                    erros.append(Erro(lado, "compilacao", "';' expected", caminho, n, 1))
                for m in re.finditer(r"ERRO:(\w+)", linha):
                    erros.append(Erro(lado, "compilacao", f"cannot find symbol {m[1]}",
                                      caminho, n, 1))
                for m in re.finditer(r"CHAMA:(\w+)", linha):
                    if f"DEF:{m[1]}" not in tudo:
                        erros.append(Erro(lado, "compilacao", f"cannot find symbol {m[1]}",
                                          caminho, n, 1))
                if "caffeine" in linha and "starter-cache" not in arquivos.get("pom.xml", ""):
                    erros.append(Erro(lado, "compilacao", "package org.springframework.cache."
                                      "caffeine does not exist", caminho, n, 1))
                if "Cannot find module" in linha:
                    erros.append(Erro(lado, "compilacao", linha.strip(), caminho, n, 1))
        if any(e.bloqueante for e in erros):
            erros = [e for e in erros if e.bloqueante]
        saida.append(Resultado(lado, not erros, ["fake build"], erros=erros))
    return saida


def projeto(tmp_path, arquivos, lado="backend"):
    """Users já implementado, Catalog é o módulo atual, Sales ainda não existe."""
    amb = Ambiente(tmp_path, contexto())
    (tmp_path / "users").mkdir()
    (tmp_path / "users" / "6_backend.json").write_text("[]")
    for caminho, texto in arquivos.items():
        destino = amb.raiz_codigo(lado) / caminho
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto)
    return amb


def etapa(amb, agentes, pendencias=None, **kw):
    return EtapaBuild(
        nome="Catalog", amb=amb, contexto=amb.contexto, agentes=agentes, metricas={},
        max_tentativas=1, plano=None, plano_base=None, pasta=amb.pasta_modulo("Catalog"),
        max_rodadas=kw.pop("max_rodadas", 3), max_pedidos=kw.pop("max_pedidos", 3),
        pendente=lambda *a: (pendencias if pendencias is not None else []).append(a),
        compilar=compilador, **kw)


def edicao(path, old, new):
    return CodeEdit(path=path, old_text=old, new_text=new)


def _codigo(amb, caminho, lado="backend"):
    return (amb.raiz_codigo(lado) / caminho).read_text()


# --------------------------------------------------------- edições
def test_aplicar_edicoes_em_ordem_no_mesmo_arquivo():
    textos = {"a.java": "x = 1;\ny = 2;\n"}
    novos, erros = aplicar_edicoes(textos.get, [edicao("a.java", "x = 1;", "x = 3;"),
                                                edicao("a.java", "x = 3;\ny", "x = 4;\nz")],
                                   lambda c: True)
    assert erros == [] and novos == {"a.java": "x = 4;\nz = 2;\n"}


def test_aplicar_edicoes_erros():
    textos = {"a.java": "x;\nx;\n", "b.java": "y"}
    _, erros = aplicar_edicoes(
        textos.get,
        [edicao("a.java", "x;", "z;"), edicao("a.java", "  3| x;", "z;"),
         edicao("b.java", "y", "w"), edicao("c.java", "q", "w"), edicao("a.java", "", "w")],
        lambda c: c != "b.java")
    assert "occurs 2 times" in erros[0]
    assert "line-number prefixes" in erros[1]
    assert "not one of your files" in erros[2] and "request_change" in erros[2]
    assert "does not exist" in erros[3]
    assert "old_text is empty" in erros[4]


# --------------------------------------------------------- autocorreção
def test_autocorrecao_compila_e_guarda_a_versao_anterior(tmp_path):
    amb = projeto(tmp_path, {"pom.xml": "<deps></deps>", SERVICO: "class S {\n  ERRO:x\n}\n"})
    agente = AgenteFalso(BuildFix(edits=[edicao(SERVICO, "  ERRO:x\n", "  ok();\n")]))
    resumo = etapa(amb, {"build:backend": agente}).rodar()

    assert resumo["ok"] and resumo["parada"] == "ok" and resumo["sessoes"] == 1
    assert [r["backend"]["erros"] for r in resumo["rodadas"]] == [1, 0]
    assert "ok();" in _codigo(amb, SERVICO)
    copia = tmp_path / "catalog" / "build" / "rodada_1" / "backend" / SERVICO
    assert "ERRO:x" in copia.read_text()
    msg = agente.chamadas[0]
    assert "MODE: BUILD FIX" in msg and "module Catalog" in msg
    assert "   2|   ERRO:x" in msg  # conteúdo com o número das linhas
    assert "cannot find symbol x" in msg
    assert [m.kind for m in amb.mensagens()] == ["build_failure"]


def test_entrega_que_deixa_erro_nos_proprios_arquivos_e_reprovada(tmp_path):
    amb = projeto(tmp_path, {SERVICO: "ERRO:a\nERRO:b\n"})
    e = etapa(amb, {})
    av = e.avaliar("backend", "Catalog", BuildFix(edits=[edicao(SERVICO, "ERRO:a", "ok")]))
    assert "remain in your files" in av.erros[0] and "symbol b" in av.erros[0]
    assert av.aplicavel  # aplica e não quebra ninguém: ainda é progresso
    av = e.avaliar("backend", "Catalog", BuildFix(edits=[]))
    assert av.erros[0].startswith("No edits were submitted.")


def test_entrega_que_quebra_outro_modulo_e_reprovada(tmp_path):
    amb = projeto(tmp_path, {SERVICO: "DEF:buscar\nERRO:x\n", USUARIO: "CHAMA:buscar\n"})
    av = etapa(amb, {}).avaliar("backend", "Catalog", BuildFix(
        edits=[edicao(SERVICO, "DEF:buscar\nERRO:x", "DEF:procurar")]))
    assert not av.aplicavel
    assert "new build errors in files you do not own" in av.erros[0]
    assert "Usuario.java" in av.erros[0]


def test_corrigir_sintaxe_pode_revelar_erros_de_outros(tmp_path):
    # o erro de sintaxe esconde o erro do Users; corrigi-lo não é culpa do Catalog
    amb = projeto(tmp_path, {SERVICO: "SINTAXE\n", USUARIO: "ERRO:u\n"})
    av = etapa(amb, {}).avaliar("backend", "Catalog",
                                BuildFix(edits=[edicao(SERVICO, "SINTAXE", "ok")]))
    assert av.erros == [] and av.aplicavel


# --------------------------------------------------------- pedido de mudança
PEDIDO_POM = ("request_change", {"path": "pom.xml",
                                 "problem": "Add spring-boot-starter-cache: package "
                                            "org.springframework.cache.caffeine does not exist"})


def test_pedido_atendido_pelo_dono_da_base(tmp_path):
    amb = projeto(tmp_path, {"pom.xml": "<deps></deps>", CACHE: "import caffeine;\n"})
    catalogo = AgenteRoteiro(([PEDIDO_POM], BuildFix(edits=[])))
    base = AgenteFalso(BuildFix(edits=[edicao("pom.xml", "<deps>", "<deps>starter-cache")],
                                reply="Added the cache starter."))
    resumo = etapa(amb, {"build:backend": catalogo, "build:backend:pedido": base}).rodar()

    assert resumo["ok"] and resumo["pedidos"] == 1 and resumo["atendidos"] == 1
    assert "starter-cache" in _codigo(amb, "pom.xml")
    assert catalogo.retornos[0].startswith("The owner (the shared base) changed pom.xml")
    pedido = base.chamadas[0]
    assert "MODE: CHANGE REQUEST" in pedido and "the shared BASE" in pedido
    assert "REQUEST FROM the backend_developer of module Catalog" in pedido
    msgs = amb.mensagens()
    assert [(m.kind, m.module, m.recipient_module) for m in msgs] == [
        ("build_failure", "Catalog", None),
        ("change_request", "Catalog", "base"),
        ("resolution", "base", "Catalog"),
    ]
    assert msgs[2].in_reply_to == msgs[1].id


def test_pedido_recusado_volta_com_a_explicacao(tmp_path):
    amb = projeto(tmp_path, {"pom.xml": "<deps></deps>", CACHE: "import caffeine;\n"})
    catalogo = AgenteRoteiro(([PEDIDO_POM], BuildFix(edits=[])))
    base = AgenteFalso(BuildFix(edits=[], reply="Use the simple cache instead."))
    pendencias = []
    resumo = etapa(amb, {"build:backend": catalogo, "build:backend:pedido": base},
                   pendencias, max_rodadas=1).rodar()

    assert not resumo["ok"] and resumo["recusados"] == 1
    assert "declined: Use the simple cache instead." in catalogo.retornos[0]
    assert [m.kind for m in amb.mensagens()][:3] == [
        "build_failure", "change_request", "declined"]
    assert pendencias and pendencias[0][0] == "build"


def test_pedido_ao_proprio_arquivo_e_ao_limite(tmp_path):
    amb = projeto(tmp_path, {"pom.xml": "<deps></deps>", SERVICO: "x"})
    e = etapa(amb, {}, max_pedidos=0)
    assert "your own files" in e.pedir("backend", "Catalog", SERVICO, "p")
    assert "does not exist" in e.pedir("backend", "Catalog", "nada.xml", "p")
    assert "budget exhausted" in e.pedir("backend", "Catalog", "pom.xml", "p")
    assert amb.mensagens() == []


def test_mudanca_do_dono_pode_quebrar_quem_pediu(tmp_path):
    # quem pediu vai se adaptar: erros nos arquivos dele não reprovam o dono
    amb = projeto(tmp_path, {USUARIO: "DEF:buscar\n", SERVICO: "CHAMA:buscar\n"})
    av = etapa(amb, {}).avaliar(
        "backend", "Users", BuildFix(edits=[edicao(USUARIO, "DEF:buscar", "DEF:procurar")]),
        tolerar="Catalog")
    assert av.erros == []


# --------------------------------------------------------- laço
def test_sem_progresso_para_e_vira_pendencia(tmp_path):
    amb = projeto(tmp_path, {SERVICO: "ERRO:x\n"})
    agente = AgenteFalso(BuildFix(edits=[]))
    pendencias = []
    resumo = etapa(amb, {"build:backend": agente}, pendencias).rodar()

    assert resumo["parada"] == "sem_progresso" and len(resumo["rodadas"]) == 2
    assert len(agente.chamadas) == 1
    [(remetente, destino, problemas, motivo)] = pendencias
    assert destino == "implementador_backend"
    assert problemas == [f"1 erro(s) de build no módulo Catalog: {SERVICO}"]
    assert motivo == "build não passou (sem_progresso)"


def test_reverter_volta_ao_inicio_da_rodada(tmp_path):
    amb = projeto(tmp_path, {SERVICO: "v0"})
    e = etapa(amb, {})
    e.rodada = 1
    e.gravar("backend", "Catalog", {SERVICO: "v1"}, "t")
    e.rodada = 2
    e.gravar("backend", "Catalog", {SERVICO: "v2"}, "t")
    e.gravar("backend", "Catalog", {SERVICO: "v3"}, "t")
    e.reverter_ate(2)
    assert _codigo(amb, SERVICO) == "v1"
    e.reverter_ate(1)
    assert _codigo(amb, SERVICO) == "v0"


def test_ordem_bloqueante_primeiro_depois_modulo_atual_e_base(tmp_path):
    amb = projeto(tmp_path, {})
    e = etapa(amb, {})
    r = Resultado("backend", False, erros=[
        Erro("backend", "compilacao", "x", USUARIO), Erro("backend", "compilacao", "x", "pom.xml"),
        Erro("backend", "compilacao", "x", SERVICO),
        Erro("backend", "compilacao", "';' expected", USUARIO)])
    assert e._ordem({"backend": r}) == [
        ("backend", "Users"), ("backend", "Catalog"), ("backend", "base")]


def test_ignora_o_que_e_de_modulo_nao_implementado(tmp_path):
    amb = projeto(tmp_path, {
        "src/App.tsx": "Cannot find module './modules/sales/routes'\n",
        "src/modules/sales/Page.tsx": "ERRO:y\n"}, lado="frontend")
    resumo = etapa(amb, {}).rodar()
    assert resumo["ok"] and resumo["sessoes"] == 0


def test_falha_de_ambiente_nao_chama_agente(tmp_path):
    amb = projeto(tmp_path, {SERVICO: "x"})
    pendencias = []

    def sem_mvn(codigo, lados=("backend",), alteracoes=None):
        return [Resultado("backend", False, erros=[
            Erro("backend", "ferramenta", "'mvn' is not available")])]
    e = etapa(amb, {}, pendencias)
    e._compilar = sem_mvn
    resumo = e.rodar()
    assert resumo["parada"] == "ambiente" and resumo["sessoes"] == 0
    assert pendencias[0][3] == "build não rodou na máquina"


# --------------------------------------------------------- no pipeline
@pytest.fixture
def com_build(monkeypatch):
    monkeypatch.setattr(pipeline, "BUILD", True)
    monkeypatch.setattr(build, "compilar", compilador)


def test_pipeline_termina_no_build(tmp_path, com_build, capsys):
    res = processar_modulo("Usuários", "d", agentes=agentes(), saida_dir=str(tmp_path))
    assert res["build"]["ok"]
    resumo = json.loads((tmp_path / "usuarios" / "8_build.json").read_text())
    assert resumo["parada"] == "ok"
    assert "[8/8] build — Usuários" in capsys.readouterr().out


def test_retomar_so_o_build(tmp_path, com_build):
    processar_modulo("Usuários", "d", agentes=agentes(), saida_dir=str(tmp_path))
    # o plano falso usa o pacote users num módulo "Usuários": o erro vai num arquivo da base
    servico = next(p for p in (tmp_path / "usuarios" / "codigo" / "backend").rglob("*.java")
                   if "/users/" not in p.as_posix())
    servico.write_text(servico.read_text() + "\n// ERRO:x\n")
    relativo = servico.relative_to(tmp_path / "usuarios" / "codigo" / "backend").as_posix()
    ags = agentes()
    ags["build:backend"] = AgenteFalso(BuildFix(edits=[edicao(relativo, "// ERRO:x\n", "")]))
    res = processar_modulo("Usuários", "d", agentes=ags, saida_dir=str(tmp_path), de="build")
    assert res["build"]["ok"] and res["build"]["sessoes"] == 1
    assert ags["planejador"].chamadas == []  # nada antes do build rodou de novo
