# Sistema multiagentes — da descrição ao código de um ERP

**Projeto:** Arquiteto-chefe (uma vez) → base de código compartilhada (uma vez)
→ pipeline de cada módulo, na ordem de construção.
**Módulo:** Requisitos → Esclarecedor → Modelador de Dados → Arquiteto de Software
→ Planejador → Implementador backend → Implementador frontend → Build.

O Arquiteto-chefe define o PROJECT CONTEXT (stack, convenções, mapa de módulos,
ordem de construção). Todos os agentes de módulo recebem esse contexto no system
prompt. O Modelador fecha o modelo de dados (tabelas, índices, integridade); o
Arquiteto recebe esse modelo já validado e projeta API, serviços públicos, eventos
e dependências sem poder alterá-lo.
O Planejador junta modelo de dados e arquitetura num plano de arquivos; os
Implementadores escrevem esses arquivos em lotes. A migration SQL é gerada em
código a partir do modelo de dados (`ddl.py`), sem LLM.
Cada agente devolve saída estruturada (`schemas.py`), validada em código
(`validation.py`) com nova tentativa automática se reprovar.
No Build (`correcao.py`), o projeto é compilado (`build.py`: `mvn compile`, `tsc`,
`vite build`). Cada Implementador corrige os erros nos próprios arquivos, com
edições por trecho. Quando a causa está num arquivo de outro módulo ou da base,
ele pede a mudança ao dono (`request_change`), que atende ou recusa explicando.

## Rodando
```bash
pip install -r requirements.txt
cp .env.example .env        # coloque sua chave

# Projeto inteiro
python main.py --projeto exemplos/erp/sistema.md --modulos-dir exemplos/erp/modulos
python main.py --projeto exemplos/erp/sistema.md --so-contexto          # só o Arquiteto-chefe
python main.py --projeto exemplos/erp/sistema.md \
               --contexto saida/sistema/0_contexto.json --apenas Users,Catalog

# Um módulo (usa exemplos/contexto_erp.json ou --contexto)
python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md                          # até o código
python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md --ate arquiteto_software  # só especificação
python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md --de planejador           # reaproveita 1–4
```
`--ate` define a última etapa (padrão: `build`); `--de` retoma a partir de uma
etapa lendo do disco os artefatos anteriores (`--de build` só compila e corrige o
código que já existe).
`VALIDAR=0` desliga a validação em código (útil para depurar prompts).
`IMPL_LOTE` (padrão 5) define quantos arquivos cada chamada de Implementador gera.
`AUTONOMIA` (padrão 1) liga as tools de leitura do ambiente e a submissão validada
dentro do loop do agente. `AUTONOMIA=0` volta ao comportamento da linha de base.
`LIMITE_CHAMADAS_AGENTE` (padrão 12) é o orçamento de chamadas ao modelo por
invocação de um agente autônomo.
`COMUNICACAO` (padrão 1) deixa os agentes perguntarem (`ask_agent`) e reportarem
bloqueios (`report_blocker`) aos donos dos artefatos anteriores do módulo.
`COMUNICACAO=0` mantém só a autonomia da F2. `MAX_CONSULTAS_AGENTE` (padrão 3) é o
número de perguntas por invocação e `MAX_REVISOES_MODULO` (padrão 2) é o número de
bloqueios atendidos por módulo; depois disso, o bloqueio vira pendência.
`BUILD` (padrão 1) liga a etapa Build; ela precisa de `mvn` e `npm` na máquina.
`MAX_RODADAS_BUILD` (padrão 3) é o número de rodadas de compilação e correção por
módulo, e `MAX_PEDIDOS_BUILD` (padrão 3) o de pedidos de mudança a outros donos.
O que não compilar vira pendência. Para compilar sem os agentes:
`python build.py saida/<projeto>/codigo --modulos Users,Catalog`.

### Linha de base (F0)
```bash
python -m scripts.linha_de_base --n 3                          # 3 execuções do projeto + resumo
python -m scripts.linha_de_base --n 3 --ate arquiteto_software # só especificação (mais barato)
python -m scripts.linha_de_base --so-agregar                   # só refaz o resumo
```
Cada execução vai para `saida/linha_de_base/execucao_NN/` com um `execucao.json`
(commit, modelo, configuração, tempo). O resumo fica em `resumo.md` e `resumo.json`,
com média e desvio por etapa, sucesso por módulo e as regras de validação que mais
reprovaram. As fichas dos agentes no modelo da IBM estão em `docs/fichas_agentes.md`.

**Custo:** as etapas de implementação fazem uma chamada por lote de arquivos.
Um módulo como Users pode passar de 10 chamadas de ~20–30 mil tokens de entrada.
Para testar prompts, prefira `--ate planejador` primeiro.

Saída do projeto em `saida/<projeto>/`: `0_contexto.json`, `contexto.md`,
`projeto.json` (status de cada módulo), `base/` (plano e manifestos da base),
`codigo/backend` e `codigo/frontend` (código de todos os módulos), `ambiente.jsonl`
(registro de cada publicação no ambiente compartilhado: quem, o quê, quando),
`mensagens.jsonl` (mensagens entre agentes: perguntas, respostas, bloqueios,
revisões, pendências, falhas de build e pedidos de mudança), `acessos.jsonl` (cada tool que um agente usou e cada
submissão, aprovada ou reprovada) e uma pasta por módulo.

### Acompanhar os agentes
```bash
python -m scripts.acompanhar saida/f3/execucao_01/sistema --seguir   # ao vivo
python -m scripts.acompanhar saida/users --modulo Users               # depois
```
Mostra, em ordem de horário, o que cada agente leu do ambiente, o que submeteu,
as mensagens trocadas e o que o orquestrador publicou. Com `LANGSMITH_TRACING=true`
no `.env`, cada chamada ao modelo e cada tool também aparecem no LangSmith.
Saída de cada módulo: `1_analise.json`, `2_especificacao.json`,
`3_modelo_dados.json`, `4_arquitetura.json`, `5_plano.json`, `6_backend.json` e
`7_frontend.json` (arquivos gerados), `relatorio.md` e `metricas.json`.
Rodando um módulo sozinho, a base, o código, o `ambiente.jsonl` e o `mensagens.jsonl`
ficam em `saida/<modulo>/`.

## Entradas
- **Descrição do sistema** (`exemplos/erp/sistema.md`): texto livre com uma seção
  `MODULES` no formato `1. Nome: responsabilidade`.
- **Descrição detalhada de módulo** (opcional): `<modulos-dir>/<slug>.md`
  (ex.: `users.md`). Sem ela, usa a linha da seção `MODULES`.

## Estrutura
- `system_prompts/` regras de cada agente + `templates.py` (mensagens com os dados)
- `agents/` um agente por arquivo (`get_agente(contexto)`) · `agents/ferramentas.py`
  tools de leitura do ambiente por papel, comunicação (`ask_agent`, `report_blocker`)
  e submissão validada no loop do agente · `agents/consultor.py` agente em modo consulta
- `schemas.py` contratos entre agentes · `validation.py` regras em código
- `projeto.py` Arquiteto-chefe + base + vários módulos · `pipeline.py` um módulo, como
  grafo LangGraph com revisão por bloqueio (`diagrama_mermaid()` desenha o grafo)
- `ambiente.py` ambiente compartilhado: o orquestrador publica o que foi aprovado;
  os agentes leem pela fachada `Leitura` (base das tools da F2)
- `implementacao.py` Planejador, Implementadores (lotes), base compartilhada
- `ddl.py` migration a partir do modelo · `layout.py` onde cada arquivo pode morar
- `assinaturas.py` assinaturas públicas de arquivos (contexto dos Implementadores)
- `execucao.py` invoca agente + valida + nova tentativa
- `scripts/acompanhar.py` linha do tempo dos agentes no ambiente (acessos, mensagens, publicações)
- `metricas.py` métricas por etapa (tentativas, reprovações, regras, tools) e agregação
  de execuções · `scripts/linha_de_base.py` linha de base (F0)
- `render.py` JSON → markdown
- `legado/` código anterior ao sistema multiagente, fora do pipeline (ver `legado/README.md`)
- `tests/` testes sem chamar LLM (agentes falsos em `tests/fakes.py`)

Mudanças recentes: ver `ALTERACOES.md`.

## Próximos passos
Ciclo de build/testes (`mvn verify`, `npm run build`) com erros devolvidos aos
Implementadores, Consolidador (coerência entre módulos), revisão humana para
alertas de confiança baixa, execução paralela (LangGraph).
