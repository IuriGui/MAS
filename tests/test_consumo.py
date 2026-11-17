"""Relato 10: contabilização, subagente desligado, contexto enxuto, documentos
em JSONL e o que o agente procurava lendo arquivos — sem chamar a API do Gemini."""
import json
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from mas.agentes import fabrica, mensagens, prompts
from mas.metricas import render_metricas
from mas.pipeline import implementacao
from mas.pipeline.execucao import _tokens, executar_agente, salvar_historico
from mas.pipeline.modulo import carregar_contexto
from mas.schemas import Endpoint, GeneratedFiles, ProjectContext
from tests.fakes import PKG, arq, arquitetura, plano

MAIN = f"src/main/java/{PKG}"


def _uso(i, o, cache=0, raciocinio=0):
    u = {"input_tokens": i, "output_tokens": o, "total_tokens": i + o,
         "input_token_details": {"cache_read": cache}}
    if raciocinio:
        u["output_token_details"] = {"reasoning": raciocinio}
    return u


# ================================================================ item 7: contabilização
def test_tokens_separa_cache_e_raciocinio():
    msgs = [AIMessage(content="a", usage_metadata=_uso(1000, 50, cache=900, raciocinio=30)),
            AIMessage(content="b", usage_metadata=_uso(2000, 20, cache=1800))]
    tk = _tokens({"messages": msgs})
    assert tk["input_tokens"] == 3000 and tk["cache_read_tokens"] == 2700
    assert tk["output_tokens"] == 70 and tk["reasoning_tokens"] == 30


class _ModeloComUso(BaseChatModel):
    """Responde em sequência; cada resposta tem usage_metadata."""
    respostas: list
    n: int = 0

    @property
    def _llm_type(self):
        return "falso"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        msg = self.respostas[min(self.n, len(self.respostas) - 1)]
        self.n += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_contador_conta_chamadas_que_nao_ficam_no_historico():
    """Uma ferramenta que chama outro modelo (como o subagente `task` ou a
    sumarização): o gasto dela não aparece nas mensagens do agente, mas entra
    nas métricas como "fora do histórico"."""
    interno = _ModeloComUso(respostas=[AIMessage(content="r", usage_metadata=_uso(1000, 50))])

    @tool
    def subtarefa() -> str:
        """Faz uma subtarefa."""
        return str(interno.invoke("x").content)

    principal = _ModeloComUso(respostas=[
        AIMessage(content="", tool_calls=[{"name": "subtarefa", "args": {}, "id": "1"}],
                  usage_metadata=_uso(100, 10)),
        AIMessage(content="fim", usage_metadata=_uso(200, 20))])
    agente = create_agent(model=principal, tools=[subtarefa])
    metricas: dict = {}
    executar_agente("t", agente, "faça", lambda o: [], metricas, 1,
                    extrair=lambda r: GeneratedFiles(files=[], notes=[]), limite_passos=10)
    m = metricas["t"]
    assert m["input_tokens"] == 1300 and m["output_tokens"] == 80
    assert m["input_tokens_fora_do_historico"] == 1000
    assert m["output_tokens_fora_do_historico"] == 50


def test_historico_de_cada_tentativa_e_gravado(tmp_path):
    destino = tmp_path / "historico" / "x.lote01.t1.json"
    salvar_historico(destino, {"messages": [
        HumanMessage(content="lote"),
        AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"file_path": "/a"},
                                           "id": "1"}]),
        ToolMessage(content="conteúdo", tool_call_id="1")]})
    dados = json.loads(destino.read_text(encoding="utf-8"))
    assert [m["type"] for m in dados["mensagens"]] == ["human", "ai", "tool"]
    salvar_historico(tmp_path / "vazio.json", {"messages": []})
    assert not (tmp_path / "vazio.json").exists()


def test_pipeline_grava_historico_por_lote(tmp_path):
    from tests.test_sandbox import sandbox_falso
    from mas.pipeline.modulo import processar_modulo
    from tests.fakes import agentes

    codigo = tmp_path / "usuarios" / "codigo"
    back = sandbox_falso(codigo)
    grafo_original = back.grafo.invoke

    def com_mensagens(entrada, config=None):
        r = grafo_original(entrada, config=config)
        return {**r, "messages": [HumanMessage(content="lote"),
                                  AIMessage(content="ok", usage_metadata=_uso(10, 1))]}
    back.grafo.invoke = com_mensagens
    processar_modulo("Usuários", "d", agentes=agentes(back=back, front=sandbox_falso(codigo)),
                     saida_dir=str(tmp_path))
    hist = sorted(p.name for p in (tmp_path / "usuarios" / "historico").iterdir())
    assert hist[0].startswith("implementador_backend.lote01.t1.")   # + carimbo da execução
    assert hist[0].endswith(".json")
    man = json.loads((tmp_path / "usuarios" / "6_backend.json").read_text())
    por_lote = [m["metricas"] for m in man if "metricas" in m]
    assert all(m["input_tokens"] == 10 and m["tentativas"] == 1 for m in por_lote)
    assert "maior_saida_tool" in por_lote[0]


def test_metricas_md_mostra_cache_e_raciocinio():
    m = {"input_tokens": 2_000_000, "output_tokens": 30_000, "segundos": 60,
         "tentativas": 1, "lotes": 1, "chamadas_modelo": 40,
         "cache_read_tokens": 1_800_000, "reasoning_tokens": 12_000,
         "input_tokens_fora_do_historico": 0, "output_tokens_fora_do_historico": 0}
    cons = {"linhas": [{"nome": "Users", "partes": {"backend": m}, "total": m}],
            "total": {"partes": {"backend": m}, "total": m}}
    md = render_metricas(cons)
    assert "| Entrada | Em cache | Saída | Raciocínio |" in md
    assert "| backend | 2,00 mi | 1,80 mi | 30 mil | 12 mil |" in md
    assert "Fora do histórico" not in md


# ================================================================ item 4: subagente
def test_subagente_general_purpose_desligado(monkeypatch):
    from deepagents.backends import StateBackend
    from langchain.chat_models import init_chat_model

    modelo = init_chat_model("google_genai:gemini-3.8-flash", google_api_key="falsa")
    monkeypatch.setattr(fabrica, "_modelo", lambda **kw: modelo)
    ag = fabrica.criar_agente_sandbox("REGRAS", carregar_contexto(), StateBackend())
    ferramentas = set(ag.grafo.nodes["tools"].bound.tools_by_name)
    assert "task" not in ferramentas
    assert {"read_file", "write_file", "execute"} <= ferramentas


# ================================================================ 2a: contexto enxuto
def test_contexto_enxuto_so_com_o_que_afeta_o_codigo():
    c = carregar_contexto()
    assert prompts.CAMPOS_CONTEXTO_IMPLEMENTADOR <= set(ProjectContext.model_fields)
    inteiro = prompts.com_contexto(c, "REGRAS")
    enxuto = prompts.com_contexto(c, "REGRAS", enxuto=True)
    assert len(enxuto) < len(inteiro) and enxuto.endswith("REGRAS")
    for fora in ('"module_map"', '"build_order"', '"decisions"', '"risks"', '"assumptions"'):
        assert fora in inteiro and fora not in enxuto
    for dentro in ('"tech_stack"', '"cross_cutting_decisions"', '"naming_conventions"'):
        assert dentro in enxuto


def test_so_os_implementadores_recebem_o_contexto_enxuto(monkeypatch):
    prompts_por_schema = {}
    monkeypatch.setattr(fabrica, "criar_agente_estruturado",
                        lambda sp, rf: prompts_por_schema.setdefault(rf.__name__, sp))
    fabrica._agente_cache.cache_clear()
    try:
        fabrica.criar_agentes(carregar_contexto())
    finally:
        fabrica._agente_cache.cache_clear()
    assert '"module_map"' not in prompts_por_schema["GeneratedFiles"]
    assert '"module_map"' in prompts_por_schema["ImplementationPlan"]


# ================================================================ 2c: JSONL
def test_documento_jsonl_um_item_por_linha():
    p = plano()
    linhas = [json.loads(l) for l in mensagens.documento_jsonl(p).splitlines()]
    backend = [l for l in linhas if l["section"] == "backend_files"]
    assert len(backend) == len(p.backend_files)
    servico = next(l for l in backend if l["path"].endswith("UserService.java"))
    assert servico["kind"] == "service" and servico["exports"] == ["UserService"]
    assert {"section": "conventions", "value": []} in linhas      # seção vazia explícita
    assert {"section": "module_name", "value": "Users"} in linhas


def test_documentos_antigos_do_mesmo_nome_sao_removidos(tmp_path):
    class Sandbox:
        def caminho_no_container(self, c):
            return "/workspace/" + Path(c).relative_to(tmp_path).as_posix()

    class Agente:
        sandbox = Sandbox()

    pasta = tmp_path / ".mas" / "usuarios"
    pasta.mkdir(parents=True)
    (pasta / "plano.json").write_text("{}")
    (pasta / "migracao.sql").write_text("CREATE TABLE x();")
    bloco = implementacao.gravar_documentos(tmp_path, "Usuários", Agente(), {
        "plano": ("plano.jsonl", "{}\n"), "vazio": ("api.txt", "")})
    assert not (pasta / "plano.json").exists() and (pasta / "plano.jsonl").exists()
    assert (pasta / "migracao.sql").exists() and not (pasta / "api.txt").exists()
    assert "/workspace/.mas/usuarios/plano.jsonl" in bloco and "output_mode" in bloco


# ================================================================ 2b: o que o agente procurava
def _plano_com_dto():
    p = plano()
    entidade = f"{MAIN}/domain/User.java"
    dto = arq(f"{MAIN}/api/dto/UserResponse.java", "dto", ["UserResponse"])
    mapper = arq(f"{MAIN}/mapper/UserMapper.java", "mapper", ["UserMapper"],
                 [entidade, dto.path])
    controller = arq(f"{MAIN}/api/UserAdminController.java", "controller",
                     ["UserAdminController"], [dto.path])
    p.backend_files += [dto, mapper, controller]
    return p, dto


def _arquitetura_com_dto():
    a = arquitetura()
    a.endpoints.append(Endpoint(
        method="GET", path="/api/v1/users/{id}", purpose="detalhe",
        requires_authentication=True, roles=["ADMIN"], request_summary="-",
        response_summary="UserResponse with id and email", http_status_codes=[200],
        tables_touched=["users"], addressed_requirements=["FR01"]))
    return a


def test_usado_por_lista_os_consumidores_do_lote():
    p, dto = _plano_com_dto()
    r = mensagens.usado_por(p.backend_files, [dto])
    assert f"- {dto.path} is used by:" in r
    assert "UserMapper.java [mapper]" in r and "UserAdminController.java [controller]" in r
    assert mensagens.usado_por(p.backend_files, [p.backend_files[-1]]) == mensagens.NO_FILES
    # entidade tem consumidores, mas a lista não vai (sem evidência de que ajude)
    entidade = p.backend_files[0]
    assert mensagens.consumidores(entidade, p.backend_files)
    assert mensagens.usado_por(p.backend_files, [entidade]) == mensagens.NO_FILES


def test_endpoints_que_usam_o_dto_do_lote():
    p, dto = _plano_com_dto()
    a = _arquitetura_com_dto()
    r = json.loads(mensagens.recorte_arquitetura(a, [dto]))
    assert [e["path"] for e in r["endpoints"]] == ["/api/v1/users/{id}"]
    # lote sem DTO: só os endpoints citados pelos arquivos
    entidade = p.backend_files[0]
    assert "endpoints" not in json.loads(mensagens.recorte_arquitetura(a, [entidade]))


def test_mensagem_do_lote_leva_usado_por(tmp_path):
    from tests.fakes import spec
    p, dto = _plano_com_dto()
    msg = mensagens.msg_implementador_backend(
        "U", spec(), _arquitetura_com_dto(), p, None, "", "", "", [dto], 1, 2,
        documentos="FULL DOCUMENTS: ...")
    assert "WHO USES THE FILES OF THIS BATCH" in msg and "UserMapper.java [mapper]" in msg
    assert '"path": "/api/v1/users/{id}"' in msg


def test_lote_de_dto_recebe_entidade_usada_pelo_mapper(tmp_path):
    p, dto = _plano_com_dto()
    entidade = tmp_path / MAIN / "domain" / "User.java"
    entidade.parent.mkdir(parents=True)
    entidade.write_text(
        f"package {PKG.replace('/', '.')}.domain;\n\n@Getter\n@Setter\n"
        "public class User {\n    private String email;\n}\n", encoding="utf-8")
    deps = implementacao.conteudo_dependencias(tmp_path, [dto], so_assinaturas=True,
                                               plano_lado=p.backend_files)
    assert f"=== {MAIN}/domain/User.java === (entity used by UserMapper" in deps
    assert "public String getEmail()" in deps and "setEmail" not in deps
    # serviço não recebe: não copia campos de entidade que não importa
    servico = next(f for f in p.backend_files if f.kind == "service")
    assert "entity used by" not in implementacao.conteudo_dependencias(
        tmp_path, [servico], so_assinaturas=True, plano_lado=p.backend_files)


# ================================================================ relato 11: leituras demais
def test_lote_com_arquivo_pesado_tem_no_maximo_dois_arquivos():
    leves = [arq(f"a/Dto{i}.java", "dto", [f"Dto{i}"]) for i in range(4)]
    pesados = [arq(f"a/S{i}.java", "service", [f"S{i}"]) for i in range(3)]
    lotes = implementacao.montar_lotes(leves + pesados, 5, 2)
    assert [len(l) for l in lotes] == [4, 2, 1]
    assert all(len(l) <= 2 for l in lotes if any(f.kind == "service" for f in l))
    # sem limite próprio (5 e 5), volta ao comportamento antigo
    assert [len(l) for l in implementacao.montar_lotes(leves + pesados, 5, 5)] == [5, 2]


def test_config_do_modulo_vai_antes_dos_servicos():
    servico = arq("a/S.java", "service", ["S"])
    config = arq("a/CacheConfig.java", "config", ["CacheConfig"])
    lotes = implementacao.montar_lotes([servico, config], 5, 1)
    assert [l[0].path for l in lotes] == ["a/CacheConfig.java", "a/S.java"]


def test_servico_recebe_as_configs_do_modulo(tmp_path):
    config = arq("a/CacheConfig.java", "config", ["CacheConfig"])
    servico = arq("a/S.java", "service", ["S"])
    dto = arq("a/D.java", "dto", ["D"])
    (tmp_path / "a").mkdir()
    (tmp_path / "a/CacheConfig.java").write_text(
        "package a;\n\npublic class CacheConfig {\n"
        "    public static final String CLIENTES = \"clientes\";\n}\n", encoding="utf-8")
    plano_lado = [config, servico, dto]
    deps = implementacao.conteudo_dependencias(tmp_path, [servico], so_assinaturas=True,
                                               plano_lado=plano_lado)
    assert "=== a/CacheConfig.java === (configuration of this module" in deps
    assert 'CLIENTES = "clientes"' in deps
    assert "CacheConfig" not in implementacao.conteudo_dependencias(
        tmp_path, [dto], so_assinaturas=True, plano_lado=plano_lado)


def test_resumo_do_raciocinio_so_no_gemini(monkeypatch):
    monkeypatch.setattr(fabrica, "MOSTRAR_RACIOCINIO", True)
    monkeypatch.setattr(fabrica, "MODEL", "google_genai:gemini-3.8-flash")
    assert fabrica._opcoes_sandbox() == {"include_thoughts": True}
    monkeypatch.setattr(fabrica, "MODEL", "openai:gpt-5.5")
    assert fabrica._opcoes_sandbox() == {}
    monkeypatch.setattr(fabrica, "MODEL", "google_genai:gemini-3.8-flash")
    monkeypatch.setattr(fabrica, "MOSTRAR_RACIOCINIO", False)
    assert fabrica._opcoes_sandbox() == {}


def test_prompt_do_sandbox_pede_leituras_juntas_e_escrita_cedo():
    regras = prompts.regras_sandbox(prompts.IMPLEMENTADOR_BACKEND)
    assert "in the SAME response" in regras and "limit=500" in regras
    assert "Never read_file a .jsonl document" in regras
    assert "Work file by file" in regras
    assert "instead of guessing its API" not in regras     # convidava a ler tudo
