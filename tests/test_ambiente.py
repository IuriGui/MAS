"""Ambiente compartilhado (F1): publicação, leitura, contratos e registro."""
import json

from ambiente import Ambiente
from pipeline import processar_modulo
from projeto import processar_projeto
from tests.test_pipeline import agentes, analise, arquitetura
from tests.test_projeto import SISTEMA, contexto

JAVA = """package com.distributor.erp.users.service;

public class UserService {
    public String nome(String id) { return id; }
    private void interno() {}
}
"""


def _ambiente(tmp_path):
    return Ambiente(tmp_path, contexto())


# --------------------------------------------------------- artefatos
def test_publica_e_le_artefato(tmp_path):
    amb = _ambiente(tmp_path)
    assert amb.artefato("Users", "analise") is None
    amb.publicar("Users", "requisitos", analise())
    assert amb.artefato("Users", "analise") == analise()
    assert (tmp_path / "users" / "1_analise.json").is_file()


def test_registro_anota_quem_publicou(tmp_path):
    amb = _ambiente(tmp_path)
    amb.publicar_contexto(contexto())
    amb.publicar("Users", "requisitos", analise())
    amb.registrar("implementador_backend", "codigo", "Users", ["A.java"])
    reg = [(r["autor"], r["tipo"], r["modulo"], r["alvos"]) for r in amb.registro()]
    assert reg == [
        ("arquiteto_chefe", "contexto", "", ["0_contexto.json"]),
        ("requisitos", "artefato", "Users", ["users/1_analise.json"]),
        ("implementador_backend", "codigo", "Users", ["A.java"]),
    ]
    assert all("quando" in r for r in amb.registro())


# --------------------------------------------------------- contratos
def test_contratos_e_endpoints_de_outros(tmp_path):
    amb = _ambiente(tmp_path)
    amb.publicar("Users", "arquiteto_software", arquitetura())
    amb.publicar("Catalog", "arquiteto_software", arquitetura(caminho="/api/v1/products"))

    assert list(amb.arquiteturas()) == ["Users", "Catalog"]  # ordem de construção
    assert amb.endpoints_de_outros("Users") == {"/api/v1/products"}
    assert amb.endpoints_de_outros("Sales") == {"/api/v1/users", "/api/v1/products"}
    contratos = amb.contratos(exceto="users")
    assert [c["module"] for c in contratos] == ["Catalog"]
    assert contratos[0]["endpoints"] == ["GET /api/v1/products"]


# --------------------------------------------------------- leitura (agentes)
def test_leitura_de_codigo(tmp_path):
    amb = _ambiente(tmp_path)
    caminho = "src/main/java/com/distributor/erp/users/service/UserService.java"
    destino = amb.raiz_codigo("backend") / caminho
    destino.parent.mkdir(parents=True)
    destino.write_text(JAVA, encoding="utf-8")
    (amb.raiz_codigo("backend") / "target").mkdir()
    (amb.raiz_codigo("backend") / "target" / "X.class").write_text("bin")

    ler = amb.leitura()
    assert ler.listar_arquivos("backend") == caminho  # ignora target/
    assert ler.listar_arquivos("backend", "src/test") == "No backend files under 'src/test'."
    assert ler.ler_arquivo("backend", caminho) == JAVA
    assert "nome(String id)" in ler.assinaturas("backend", caminho)
    assert "interno" not in ler.assinaturas("backend", caminho)


def test_leitura_nao_sai_da_raiz(tmp_path):
    amb = _ambiente(tmp_path)
    (tmp_path / "segredo.txt").write_text("x")
    amb.raiz_codigo("backend").mkdir(parents=True)
    ler = amb.leitura()
    assert "does not exist" in ler.ler_arquivo("backend", "../../segredo.txt")
    assert "Unknown side" in ler.ler_arquivo("banco", "x")


def test_leitura_de_artefatos_e_contexto(tmp_path):
    amb = _ambiente(tmp_path)
    ler = amb.leitura()
    assert "has not published 'analise'" in ler.artefato("Users", "analise")
    assert "Unknown artifact" in ler.artefato("Users", "codigo")
    amb.publicar("Users", "requisitos", analise())
    assert json.loads(ler.artefato("Users", "analise"))["module_name"] == "Users"
    assert json.loads(ler.contexto())["build_order"] == ["Users", "Catalog", "Sales"]
    assert "No PROJECT CONTEXT" in Ambiente(tmp_path).leitura().contexto()


# --------------------------------------------------------- integração
def test_modulo_sozinho_registra_cada_etapa(tmp_path):
    processar_modulo("Usuários", "d", agentes=agentes(), saida_dir=str(tmp_path))
    reg = Ambiente(tmp_path, registro=tmp_path / "usuarios" / "ambiente.jsonl").registro()
    autores = [(r["autor"], r["modulo"]) for r in reg]
    assert autores == [
        ("requisitos", "Usuários"), ("esclarecedor", "Usuários"),
        ("modelador_dados", "Usuários"), ("arquiteto_software", "Usuários"),
        ("planejador", "base"), ("implementador_backend", "base"),
        ("implementador_frontend", "base"),
        ("planejador", "Usuários"), ("implementador_backend", "Usuários"),
        ("implementador_frontend", "Usuários"),
    ]


def test_projeto_usa_um_ambiente_so(tmp_path):
    processar_projeto("Loja", SISTEMA, contexto=contexto(), ate="arquiteto_software",
                      fabrica_agentes=lambda c: agentes(), saida_dir=str(tmp_path))
    amb = Ambiente(tmp_path / "loja", contexto())
    assert list(amb.arquiteturas()) == ["Users", "Catalog", "Sales"]
    modulos = [r["modulo"] for r in amb.registro()]
    assert modulos == ["Users"] * 4 + ["Catalog"] * 4 + ["Sales"] * 4
