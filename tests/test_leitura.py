"""Leitura em lote (read_files, get_signatures): vários arquivos numa chamada."""
from agents import ferramentas
from ambiente import Ambiente
from tests.test_projeto import contexto

JAVA = """package com.distributor.erp.users.service;

public class {nome} {{
    public String nome(String id) {{ return id; }}
    private void interno() {{}}
}}
"""
BASE = "src/main/java/com/distributor/erp/users/service"


def _ambiente(tmp_path, *nomes):
    amb = Ambiente(tmp_path, contexto())
    for n in nomes:
        destino = amb.raiz_codigo("backend") / BASE / f"{n}.java"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(JAVA.format(nome=n), encoding="utf-8")
    return amb


def test_le_varios_arquivos_numa_resposta(tmp_path):
    ler = _ambiente(tmp_path, "A", "B").leitura()
    txt = ler.ler_arquivos("backend", [f"{BASE}/A.java", f"{BASE}/B.java"], limite=10_000)
    assert f"=== {BASE}/A.java ===\n" in txt and f"=== {BASE}/B.java ===\n" in txt
    assert "public class A" in txt and "public class B" in txt
    assert "private void interno" in txt  # conteúdo integral


def test_acima_do_limite_vem_so_assinatura(tmp_path):
    ler = _ambiente(tmp_path, "A", "B").leitura()
    um = len(JAVA.format(nome="A")) + len(f"=== {BASE}/A.java ===\n")
    txt = ler.ler_arquivos("backend", [f"{BASE}/A.java", f"{BASE}/B.java"], limite=um + 10)
    a, b = txt.split(f"=== {BASE}/B.java ===")
    assert "private void interno" in a                  # A inteiro
    assert "public signatures only" in b                # B resumido e avisado
    assert "nome(String id)" in b and "interno" not in b


def test_arquivos_inexistentes_repetidos_e_vazios(tmp_path):
    ler = _ambiente(tmp_path, "A").leitura()
    txt = ler.ler_arquivos("backend", [f"{BASE}/A.java", f"{BASE}/A.java", " ",
                                       "nao/Existe.java"], limite=10_000)
    assert txt.count(f"=== {BASE}/A.java ===") == 1
    assert "Files that do not exist (backend): ['nao/Existe.java']" in txt
    assert ler.ler_arquivos("backend", [], limite=10) == "Pass at least one file path."
    assert "Unknown side" in ler.ler_arquivos("banco", ["x"], limite=10)


def test_nao_sai_da_raiz(tmp_path):
    (tmp_path / "segredo.txt").write_text("senha")
    ler = _ambiente(tmp_path, "A").leitura()
    txt = ler.ler_arquivos("backend", ["../../segredo.txt"], limite=10_000)
    assert "senha" not in txt and "do not exist" in txt


def test_assinaturas_de_varios(tmp_path):
    ler = _ambiente(tmp_path, "A", "B").leitura()
    txt = ler.assinaturas_de("backend", [f"{BASE}/A.java", f"{BASE}/B.java", "x.java"])
    assert txt.count("nome(String id)") == 2 and "interno" not in txt
    assert "File 'x.java' does not exist" in txt


def test_tools_em_lote_na_sessao(tmp_path):
    amb = _ambiente(tmp_path, "A", "B")
    s = ferramentas.Sessao(etapa="implementador_backend", leitura=amb.leitura(),
                           modulo="Users", lado="backend")
    with ferramentas.sessao(s):
        txt = ferramentas.read_files.invoke({"paths": [f"{BASE}/A.java", f"{BASE}/B.java"]})
        sig = ferramentas.get_signatures.invoke({"paths": [f"{BASE}/A.java"]})
    assert "public class A" in txt and "public class B" in txt
    assert "nome(String id)" in sig
    acessos = amb.acessos()
    assert [a["acao"] for a in acessos] == ["read_files", "get_signatures"]
    assert acessos[0]["args"]["paths"] == [f"{BASE}/A.java", f"{BASE}/B.java"]
    assert ferramentas.read_files.args["paths"]["type"] == "array"


def test_instrucoes_pedem_leitura_em_lote():
    texto = ferramentas.instrucoes(ferramentas.para("implementador_backend"),
                                   "GeneratedFiles", 12)
    assert "ONE read_files" in texto
    assert "read_files" not in ferramentas.instrucoes(
        ferramentas.para("requisitos"), "ModuleAnalysis", 12)
