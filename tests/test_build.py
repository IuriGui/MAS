import subprocess

import build
from build import (Erro, Resultado, dono, erros_maven, erros_npm, erros_tsc,
                        erros_vite, para_agente, por_modulo)

RAIZ = "/tmp/mvn_x/backend"
JAVA = "src/main/java/br/com/company/erp"

# trechos de logs reais do código gerado em saida/impl_f3
LOG_JAVAC = f"""\
[INFO] Compiling 160 source files with javac [debug release 21] to target/classes
[INFO] -------------------------------------------------------------
[ERROR] COMPILATION ERROR :
[INFO] -------------------------------------------------------------
[ERROR] {RAIZ}/{JAVA}/inventory/application/service/StockReservationService.java:[144,16] constructor StockReservationResultDto in record br.com.company.erp.inventory.internal.dto.InventoryInternalDtos.StockReservationResultDto cannot be applied to given types;
  required: java.util.UUID,java.math.BigDecimal,java.time.Instant
  found:    java.util.UUID,java.math.BigDecimal
  reason: actual and formal argument lists differ in length
[ERROR] {RAIZ}/{JAVA}/users/application/AuthenticationService.java:[153,31] incompatible types: java.util.UUID cannot be converted to java.lang.String
[INFO] 2 errors
[INFO] BUILD FAILURE
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.13.0:compile (default-compile) on project erp-monolith: Compilation failure: Compilation failure:
[ERROR] {RAIZ}/{JAVA}/inventory/application/service/StockReservationService.java:[144,16] constructor StockReservationResultDto in record br.com.company.erp.inventory.internal.dto.InventoryInternalDtos.StockReservationResultDto cannot be applied to given types;
[ERROR]   required: java.util.UUID,java.math.BigDecimal,java.time.Instant
[ERROR] {RAIZ}/{JAVA}/users/application/AuthenticationService.java:[153,31] incompatible types: java.util.UUID cannot be converted to java.lang.String
[ERROR] -> [Help 1]
[ERROR]
[ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.
"""

LOG_SINTAXE = f"""\
[ERROR] COMPILATION ERROR :
[ERROR] {RAIZ}/{JAVA}/catalog/application/service/UnitOfMeasureService.java:[7,56] ';' expected
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.13.0:compile (default-compile) on project erp-monolith: Compilation failure
[ERROR] {RAIZ}/{JAVA}/catalog/application/service/UnitOfMeasureService.java:[7,56] ';' expected
[ERROR] -> [Help 1]
"""

LOG_POM = f"""\
[INFO] Scanning for projects...
[ERROR] [ERROR] Some problems were encountered while processing the POMs:
[ERROR] 'dependencies.dependency.version' for io.jsonwebtoken:jjwt-api:jar is missing. @ line 73, column 21
 @
[ERROR] The build could not read 1 project -> [Help 1]
[ERROR]
[ERROR]   The project br.com.company.erp:erp-monolith:1.0.0-SNAPSHOT ({RAIZ}/pom.xml) has 1 error
[ERROR]     'dependencies.dependency.version' for io.jsonwebtoken:jjwt-api:jar is missing. @ line 73, column 21
"""

LOG_DEP = """\
[ERROR] Failed to execute goal on project erp-monolith: Could not resolve dependencies for project br.com.company.erp:erp-monolith:jar:1.0.0-SNAPSHOT: Could not find artifact io.jsonwebtoken:jjwt-apix:jar:9.9 in central (https://repo.maven.apache.org/maven2) -> [Help 1]
[ERROR]
"""

LOG_TSC = """\
src/context/AuthContext.tsx(26,13): error TS2304: Cannot find name 'r'.
src/modules/inventory/pages/StockAdjustmentListPage.tsx(513,33): error TS2322: Type 'boolean | null' is not assignable to type 'boolean | undefined'.
  Type 'null' is not assignable to type 'boolean | undefined'.
src/modules/users/routes.tsx(1,1): error TS6133: 'React' is declared but its value is never read.
"""


# ------------------------------------------------------------- javac / Maven
def test_javac_erros_com_posicao_e_caminho_relativo():
    erros = erros_maven(LOG_JAVAC, RAIZ)
    assert [(e.arquivo.rsplit("/", 1)[1], e.linha, e.coluna) for e in erros] == [
        ("StockReservationService.java", 144, 16), ("AuthenticationService.java", 153, 31)]
    assert erros[0].arquivo.startswith("src/main/java/")
    assert all(e.tipo == "compilacao" and e.lado == "backend" for e in erros)


def test_javac_guarda_as_linhas_de_detalhe():
    detalhe = erros_maven(LOG_JAVAC, RAIZ)[0].detalhe.splitlines()
    assert detalhe[0].startswith("required:")
    assert detalhe[-1] == "reason: actual and formal argument lists differ in length"


def test_javac_resumo_do_maven_nao_duplica():
    assert len(erros_maven(LOG_JAVAC, RAIZ)) == 2
    assert len(erros_maven(LOG_SINTAXE, RAIZ)) == 1


def test_pom_invalido():
    [e] = erros_maven(LOG_POM, RAIZ)
    assert (e.tipo, e.arquivo, e.linha, e.coluna) == ("pom", "pom.xml", 73, 21)
    assert "jjwt-api" in e.mensagem


def test_dependencia_nao_resolvida():
    [e] = erros_maven(LOG_DEP)
    assert e.tipo == "dependencia"
    assert "jjwt-apix" in e.mensagem and "[Help" not in e.mensagem


def test_maven_ok_sem_erros():
    assert erros_maven("[INFO] BUILD SUCCESS\n") == []


# ------------------------------------------------------------- tsc / vite / npm
def test_tsc():
    erros = erros_tsc(LOG_TSC)
    assert [(e.arquivo, e.linha, e.codigo) for e in erros] == [
        ("src/context/AuthContext.tsx", 26, "TS2304"),
        ("src/modules/inventory/pages/StockAdjustmentListPage.tsx", 513, "TS2322"),
        ("src/modules/users/routes.tsx", 1, "TS6133")]
    assert erros[1].detalhe == "Type 'null' is not assignable to type 'boolean | undefined'."


def test_tsc_erro_sem_arquivo():
    [e] = erros_tsc("error TS5058: The specified path does not exist: 'tsconfig.json'.\n")
    assert e.codigo == "TS5058" and e.arquivo == ""


def test_vite_import_inexistente():
    log = ('error during build:\n[vite]: Rollup failed to resolve import "./Nada" from '
           '"/tmp/f/frontend/src/App.tsx".\n    at viteWarn (file:///x/vite.js:1:1)\n')
    [e] = erros_vite(log, "/tmp/f/frontend")
    assert e.arquivo == "src/App.tsx"
    assert "/tmp/f" not in e.mensagem


def test_npm_versao_inexistente():
    log = ("npm error code ETARGET\nnpm error notarget No matching version found for vite@^99.\n"
           "npm error A complete log of this run can be found in: /x.log\n")
    [e] = erros_npm(log)
    assert e.tipo == "dependencia" and "vite@^99" in e.mensagem and "complete log" not in e.mensagem


# ------------------------------------------------------------- dono e mensagem
MODULOS = ["Users", "Catalog", "Inventory"]


def test_dono_pelo_caminho():
    assert dono(f"{JAVA}/users/application/AuthenticationService.java", MODULOS) == "Users"
    assert dono("src/modules/inventory/routes.tsx", MODULOS) == "Inventory"
    assert dono("src/context/AuthContext.tsx", MODULOS) == "base"
    assert dono("pom.xml", MODULOS) == "base"


def test_por_modulo():
    r = [Resultado("backend", False, erros=erros_maven(LOG_JAVAC, RAIZ)),
         Resultado("frontend", False, erros=erros_tsc(LOG_TSC))]
    assert {m: len(e) for m, e in por_modulo(r, MODULOS).items()} == {
        "Inventory": 2, "Users": 2, "base": 1}


def test_para_agente_agrupa_por_arquivo_e_encurta_tipos():
    texto = para_agente([Resultado("backend", False, ["mvn compile"],
                                   erros=erros_maven(LOG_JAVAC, RAIZ))])
    assert texto.startswith("BUILD FAILED - backend (mvn compile): 2 error(s) in 2 file(s)")
    assert "L153:31 incompatible types: UUID cannot be converted to String" in texto
    assert "java.util" not in texto


def test_para_agente_filtra_pelos_arquivos_do_agente():
    erros = erros_tsc(LOG_TSC)
    texto = para_agente([Resultado("frontend", False, erros=erros)],
                        arquivos={"src/modules/users/routes.tsx"})
    assert "1 in your files, 2 elsewhere" in texto
    assert "AuthContext" not in texto


def test_para_agente_avisa_sobre_erro_de_sintaxe():
    texto = para_agente([Resultado("backend", False, erros=erros_maven(LOG_SINTAXE, RAIZ))])
    assert "javac stops at syntax errors" in texto


def test_para_agente_limite():
    erros = [Erro("frontend", "compilacao", f"e{i}", "a.ts", i, 1) for i in range(5)]
    texto = para_agente([Resultado("frontend", False, erros=erros)], limite=2)
    assert "... and 3 more error(s)" in texto


def test_para_agente_ok_e_falha_de_ambiente():
    ok = Resultado("frontend", True, ["tsc --noEmit"], 3.0)
    amb = Resultado("backend", False, erros=[Erro("backend", "ferramenta", "'mvn' is not available")])
    texto = para_agente([amb, ok])
    assert "BUILD NOT RUN - backend: environment problem, not your code." in texto
    assert "BUILD OK - frontend (tsc --noEmit, 3s)" in texto


# ------------------------------------------------------------- execução (sem mvn de verdade)
def test_backend_sem_pom(tmp_path):
    r = build.compilar_backend(tmp_path)
    assert not r.ok and r.erros[0].arquivo == "pom.xml"


def test_backend_mvn_ausente(tmp_path, monkeypatch):
    (tmp_path / "pom.xml").write_text("<project/>")

    def falha(*a, **k):
        raise FileNotFoundError("mvn")
    monkeypatch.setattr(build, "_rodar", falha)
    r = build.compilar_backend(tmp_path)
    assert r.falha_de_ambiente


def test_backend_compila_numa_copia(tmp_path, monkeypatch):
    (tmp_path / "pom.xml").write_text("<project/>")
    vistos = []

    def falso(cmd, cwd, timeout):
        vistos.append(cwd)
        (cwd / "target").mkdir()
        return subprocess.CompletedProcess(cmd, 1, f"[ERROR] {cwd}/src/A.java:[1,2] boom\n", "")
    monkeypatch.setattr(build, "_rodar", falso)
    r = build.compilar_backend(tmp_path, offline=True)
    assert vistos[0] != tmp_path and not (tmp_path / "target").exists()
    assert r.erros[0].arquivo == "src/A.java"
    assert r.passos[0].startswith("mvn -o ")
