# Alterações

- [Rodada 7 — F1: ambiente compartilhado](#rodada-7--f1-ambiente-compartilhado)
- [Rodada 6 — F0: linha de base para o sistema multiagente](#rodada-6--f0-linha-de-base-para-o-sistema-multiagente)
- [Rodada 5 — Regeneração do módulo e aplicação no ar](#rodada-5--regeneração-do-módulo-e-aplicação-no-ar)
- [Rodada 4 — Execução completa, compilação do código gerado e correções](#rodada-4--execução-completa-compilação-do-código-gerado-e-correções)
- [Rodada 3 — Limites contra repetição sem fim e primeira execução real](#rodada-3--limites-contra-repetição-sem-fim-e-primeira-execução-real)
- [Rodada 2 — Planejador e Implementadores (backend e frontend)](#rodada-2--planejador-e-implementadores-backend-e-frontend)
- [Rodada 1 — Integração do Arquiteto-chefe e execução por projeto](#rodada-1--integração-do-arquiteto-chefe-e-execução-por-projeto)

---

## Rodada 7 — F1: ambiente compartilhado

Data: 14/10/2026.

Segunda fase do plano. Ela cria o ambiente compartilhado que a IBM descreve como
meio de comunicação indireta: um agente modifica o ambiente e os outros percebem
a modificação. Os agentes ainda não leem o ambiente. Isso vem na F2, com as tools.

### `ambiente.py`
- **`Ambiente`** é o quadro do projeto. Ele guarda o PROJECT CONTEXT, os artefatos de
  cada módulo, os contratos publicados (serviços públicos, eventos e endpoints) e
  o código gerado. O disco continua sendo a única fonte da verdade, com o mesmo
  layout de antes.
- **Só o orquestrador escreve**, com `publicar_contexto`, `publicar` e `registrar`, e
  só saídas aprovadas na validação. Cada escrita vai para `ambiente.jsonl`, com
  autor, tipo, módulo, alvos e horário. A base compartilhada também é registrada.
- **`Leitura`** é a visão só de leitura para os agentes: contexto, artefatos,
  contratos, lista de arquivos, conteúdo e assinaturas públicas. Ela devolve texto
  e mensagens em inglês, e recusa caminhos que saem da raiz do código. As tools da
  F2 serão uma camada fina sobre ela.

### O que mudou no orquestrador
- `pipeline.py` publica e lê artefatos pelo ambiente. A tabela `ARTEFATOS` passou
  para `ambiente.py`.
- `projeto.py` usa um único ambiente para o projeto todo. Os contratos dos outros
  módulos são lidos do ambiente a cada módulo, em vez de ficarem num dicionário em
  memória. `_arquiteturas_existentes` foi removida.
- `implementacao.caminhos_em_disco` passou para `ambiente.py`.

### Verificação
Uma execução do projeto com agentes falsos, no código anterior e no novo, gerou os
mesmos 59 arquivos com o mesmo conteúdo. O `ambiente.jsonl` é a única novidade.

**Uma diferença de comportamento:** se um módulo falha depois do Arquiteto de
software (por exemplo, no Planejador), a arquitetura dele já foi aprovada e
publicada. Os módulos seguintes passam a vê-la como contrato. Antes, ela só entrava
no resumo dos outros módulos se o módulo inteiro terminasse.

---

## Rodada 6 — F0: linha de base para o sistema multiagente

Data: 14/10/2026.

Primeira fase do plano para tornar o pipeline um sistema multiagente, com base nas
definições da IBM. Esta fase não muda o comportamento dos agentes. Ela só mede o
sistema atual, para que as fases seguintes tenham com o que ser comparadas.

### Métricas novas por etapa (`metricas.py`, registradas em `execucao.py`)
- `reprovadas`: tentativas reprovadas na validação em código.
- `sem_saida`: tentativas sem saída estruturada.
- `falhas_finais`: vezes em que a etapa (ou um lote) esgotou as tentativas.
- `chamadas_tool`: tool calls do agente, fora a da saída estruturada. Hoje é sempre 0.
- `erros_por_regra`: quantas vezes cada regra de `validation.py` reprovou uma saída.
  A regra é a mensagem de erro sem os valores concretos (nomes, listas, números).

Os campos antigos (`tentativas`, tokens, `segundos`, `lotes`) não mudaram. Métricas
gravadas antes desta rodada ganham os campos novos ao serem retomadas.

### Linha de base (`scripts/linha_de_base.py`)
Roda o projeto N vezes com a mesma entrada, cada vez em
`saida/linha_de_base/execucao_NN/`, e gera `resumo.md` e `resumo.json`. O resumo
traz média e desvio por etapa, sucesso por módulo e as regras que mais reprovaram.

### Fichas dos agentes (`docs/fichas_agentes.md`)
Cada agente descrito pelos elementos da IBM (modelo, objetivo, ambiente, percepção,
saída), com o resumo do que ainda falta para um sistema multiagente.

### Mensagens de erro em inglês
As mensagens de `validation.py`, que voltam aos agentes nas novas tentativas,
passaram a ser em inglês, como os system prompts. Os testes foram ajustados.

---

## Rodada 5 — Regeneração do módulo e aplicação no ar

Data: 08/10/2026.

### Regeneração com as correções da rodada 4

O código do módulo Users foi regenerado com as correções da rodada 4:
assinaturas da base e dependências que não são mais cortadas. A especificação, o
plano e a base foram reaproveitados.

```
SAIDA_DIR=saida/execucoes/20261008 python3 main.py --nome Users \
    --arquivo exemplos/erp/modulos/users.md --de implementador_backend
```
Log: `saida/execucoes/20261008_regeneracao.log`. O código anterior foi guardado em
`saida/execucoes/20261008/snapshots/`.

| Etapa | Lotes | Tentativas | Tokens (entrada / saída) | Tempo |
|---|---|---|---|---|
| Backend do módulo | 8 | 9 | 364 mil / 99 mil | 3.815 s |
| Frontend do módulo | 3 | 3 | 81 mil / 41 mil | 219 s |
| **Total** | | | **445 mil / 140 mil ≈ US$ 0,86** | |

- O **backend** levou cerca de 64 minutos, contra 11 na execução anterior. O log
  não mostra erro de cota nem repetição HTTP, e a causa não foi identificada. A
  hipótese mais provável é a latência do modelo com contexto ~30% maior.
- O **lote 7 do backend** veio com deslocamento em cadeia: cada arquivo tinha o
  conteúdo do vizinho. O reparo da época movia arquivos só para caminhos
  faltantes e não resolveu; o retry resolveu.
- **Melhoria posterior** em `reparar_caminhos`: em Java/Kotlin, todo arquivo que
  declara `P.X` vai para o caminho pedido de `P.X`, mesmo que esteja ocupado. O
  caminho é função do pacote e do tipo, então a reatribuição é sempre correta;
  ela não se aplica quando dois arquivos declaram o mesmo tipo. Testado com o
  padrão em cadeia.
- O LangSmith falhou ao enviar rastros (erro de certificado SSL). As chamadas
  ao Gemini não foram afetadas.

### Compilação: antes e depois

| | Rodada 4 | Rodada 5 |
|---|---|---|
| Backend, código principal | 14 erros (3 arquivos) | **0 erros** (58 classes) |
| Backend, testes | (não chegou a compilar) | 7 erros em `UserControllerTest.java` (aridade de construtores de DTO) |
| Frontend (`tsc`) | 11 erros | 3 erros (1 da base, 2 imports sem uso) |

### Aplicação no ar

A infraestrutura local usa contêineres próprios, sem tocar nos Postgres de
outros projetos:
- `sis-erp-postgres`: Postgres 16 na porta 5435;
- `sis-erp-redis`: Redis 7 na porta 6379.

O backend roda em `java -jar` na porta 8080 e o frontend em `vite` na porta 5173,
com proxy de `/api` para a 8080.

Verificado, chamando a API:
- `actuator/health` responde 200;
- as migrations Flyway são aplicadas na subida;
- o login devolve um JWT com os papéis;
- o login inválido é recusado com 401 no formato RFC 7807;
- rota sem token é recusada com 401;
- `POST /users` cria o usuário (201), recusa senha curta (400, regra da
  especificação) e recusa e-mail repetido (409);
- `GET /users` funciona com busca e com filtro de status;
- `GET /roles` e `GET /permissions` respondem 200;
- `npm run build` passa;
- o login funciona pelo proxy do Vite.

### Correções manuais (não geradas pela pipeline)

Todas estão marcadas com `CORREÇÃO MANUAL` ou `SEED MANUAL` nos arquivos.

| # | Arquivo | Problema | Origem |
|---|---|---|---|
| 1 | `db/migration/V900__correcao_manual_colunas_faltantes.sql` (novo) | `outbox_events.status` mapeado na entidade e ausente no SQL da base; `deleted_at` mapeado pela `BaseEntity` para todas as tabelas, mas o modelo só prevê soft delete em `users` e `roles` | incoerência entre a base e o modelo de dados |
| 2 | `seed_admin_manual.sql` (fora das migrations) | não há administrador inicial, e ninguém consegue entrar | lacuna da especificação |
| 3 | `UserRepository.java` | `lower(bytea)`: `:search` nulo sem tipo no PostgreSQL | detalhe de Hibernate 6 + PostgreSQL |
| 4 | `frontend/src/vite-env.d.ts` (novo) | `import.meta.env` sem tipo | plano da base (já corrigido no prompt; a base não foi regenerada) |
| 5–6 | `routes.tsx`, `UserListPage.tsx` | imports sem uso (TS6133) | modo estrito |

Dois desses erros a pipeline poderia detectar sozinha. Os descompassos entre
entidades e migrations (nº 1) foram achados gerando o schema esperado pelo
Hibernate e comparando com o banco. Isso pode virar uma validação na pipeline,
junto com a compilação.

### Pendências

- `UserControllerTest.java` não compila; para subir a aplicação, o jar foi gerado
  com `-Dmaven.test.skip=true`.
- A tela de login exige o campo **tenant**
  (`018f0000-0000-7000-8000-000000000001`), coerente com o multi-tenant, mas
  pouco prático com um tenant só.
- Não há tabela de permissões populada (`GET /permissions` vazio): a
  especificação não define a lista inicial.

---

## Rodada 4 — Execução completa, compilação do código gerado e correções

Data: 08/10/2026.

### Correções antes da execução

- **Reparo de caminho trocado** (`implementacao.reparar_caminhos`): resolve a
  causa da falha da rodada 3. Quando o modelo escreve a classe `P.X` com o
  `path` de outro arquivo do lote e `P.X` está faltando, o código corrige o
  `path` antes de validar, sem nova chamada ao modelo. A saída real que falhou
  virou caso de teste (`tests/dados/lote_caminho_trocado.json`).
- **Retomada por arquivo**: cada lote aprovado é registrado em
  `<etapa>.progresso.json`, e uma nova execução gera só os arquivos pendentes.
- **Falsos positivos**:
  - `TODO`/`FIXME` só são detectados em maiúsculas, porque "todo" é uma palavra
    comum em textos de tela em português;
  - `package-info.java` não precisa declarar um tipo;
  - imports de arquivos `.d.ts` passaram a ser reconhecidos.
- **URLs da API no frontend da base**: a validação reprovaria sempre o login,
  porque a base não conhecia nenhum endpoint. Agora a base recebe os endpoints
  das arquiteturas conhecidas e, sem nenhuma, não tem as URLs checadas.

### Segunda execução real (retomada)

A retomada continuou a execução da rodada 3, com `--de planejador`. Os
artefatos 1–4 e os 25 arquivos já aprovados da base foram reaproveitados.

```
SAIDA_DIR=saida/execucoes/20261008 python3 main.py --nome Users \
    --arquivo exemplos/erp/modulos/users.md --de planejador
```
Log: `saida/execucoes/20261008_retomada.log`.

| Etapa | Lotes | Tentativas | Tokens (entrada / saída) | Tempo |
|---|---|---|---|---|
| Backend da base (3 arquivos pendentes) | 1 | 1 | 13 mil / 5 mil | 32 s |
| Frontend da base | 4 | 4 | 40 mil / 19 mil | 121 s |
| Planejador do módulo | — | 1 | 26 mil / 11 mil | 68 s |
| Backend do módulo | 8 | 8 | 259 mil / 108 mil | 672 s |
| Frontend do módulo | 3 | 4 | 85 mil / 56 mil | 325 s |

**A pipeline completou**: 65 arquivos de backend e 30 de frontend em
`saida/execucoes/20261008/users/codigo/`. Houve uma reprovação, no frontend do
módulo: o mesmo erro de caminho trocado, agora num `.tsx`. O retry resolveu. Na
época, o reparo automático cobria só Java/Kotlin.

### O código gerado compila? Ainda não

Verificação feita à mão, fora da pipeline:
- backend: `mvn test-compile`;
- frontend: `npm install --ignore-scripts` e `tsc --noEmit`.

| Lado | Resultado |
|---|---|
| Backend | **Falha**: 14 erros distintos, em 3 arquivos do módulo. **A base compilou sem erros.** |
| Frontend | **Falha**: 11 erros (`tsc`). |

Todos os erros têm a mesma raiz: **o modelo não viu a API que estava usando**.
- **Limite de contexto baixo demais (bug da pipeline):** o lote dos serviços
  dependia de 16 arquivos, e o limite de 60 mil caracteres cortou 9 deles,
  inclusive todos os repositórios. O modelo inventou nomes de métodos
  (`findByTenantIdAndIdIn` no lugar do real `findAllByTenantIdAndIdInAndDeletedAtIsNull`).
- **Base mostrada só por caminho e `exports`:** o modelo errou
  - o construtor de `BusinessException`;
  - as props de `ProtectedRoute` (`roles` em vez de `requiredRoles`);
  - os campos de `PaginatedResponse` (`total_elements` em vez de `totalElements`).
- **Menores:** faltou o `vite-env.d.ts` na base (`import.meta.env`), e havia
  variáveis sem uso, que reprovam no modo estrito do TypeScript.

### Correções depois da execução (ainda não testadas com o Gemini)

- **`assinaturas.py`** (novo): extrai as assinaturas públicas de um arquivo:
  - Java/Kotlin: construtores, métodos, campos e métodos de interface;
  - TS/JS: blocos `interface`/`type`/`enum` inteiros e a primeira linha de cada
    `export`.
- **Dependências nunca são omitidas:** até o limite vão inteiras; depois dele,
  só com as assinaturas. O limite subiu para 400 mil caracteres (≈100 mil
  tokens; o Gemini aceita ~1M).
- **API da base em todo lote de módulo:** a seção "BASE API" traz as assinaturas
  de todos os arquivos da base.
- **Prompts:**
  - nunca chamar método que não apareça no código ou nas assinaturas mostradas;
  - sem imports e variáveis sem uso;
  - código compatível com o compilador estrito;
  - a base inclui as declarações de tipo que a ferramenta de build exige.
- **Reparo de caminho também no frontend:** o arquivo é reatribuído quando
  exporta todos os `exports` planejados de exatamente um arquivo que falta.

São 8 testes novos; 102 no total, todos passando sem chamar o Gemini.

### Próximo passo recomendado

**Ciclo de compilação na pipeline.** Depois de cada lado, rodar
`mvn test-compile` ou `tsc --noEmit`, mapear cada erro para o arquivo que o
causou e devolver ao Implementador só esses arquivos, com os erros e um limite de
rodadas. As correções acima reduzem a causa, mas só a compilação garante que o
resultado compila.

---

## Rodada 3 — Limites contra repetição sem fim e primeira execução real

Data: 08/10/2026. Projeto versionado com git a partir desta rodada.

### Problema encontrado

O `create_agent` do LangChain define `recursion_limit=9999`. Com o Gemini, a saída
estruturada usa *tool strategy* com `handle_errors=True`. Assim, se o modelo erra
o formato, a biblioteca chama o modelo de novo **até ~9.999 passos**, fora do
nosso controle de tentativas. Isso foi reproduzido com um modelo falso que sempre
erra: o teste foi interrompido à força em 60 chamadas. Além disso, a chamada HTTP
não tinha timeout.

### Limites (todos finitos)

| Onde | Limite | Configuração |
|---|---|---|
| Chamadas ao modelo por tentativa | 3 (resposta + 1 correção de formato interna) | `LIMITE_PASSOS_AGENTE` (mín. 2) |
| Tentativas por etapa ou lote | 2 | `MAX_TENTATIVAS` (mín. 1) |
| Repetições em erro HTTP | 3 | `MAX_RETRIES_API` |
| Tempo por chamada HTTP | 600 s | `TIMEOUT_API` |
| Arquivos por lote | 5 (mín. 1; com 0, o laço de lotes não avançava) | `IMPL_LOTE` |

O pior caso por etapa ou lote é `MAX_TENTATIVAS × LIMITE_PASSOS_AGENTE` = 6
chamadas ao modelo. Quando o modelo não produz saída estruturada, a tentativa
conta como reprovada. Se nenhuma tentativa produz saída, a etapa falha com
`PipelineError`; antes, a pipeline quebrava com `KeyError`.

Testes em `tests/test_limites.py` (9). Eles usam o `create_agent` real com
modelos falsos e cobrem:
- formato sempre errado: exatamente 2 × 3 chamadas e depois erro;
- formato corrigido dentro da mesma tentativa;
- formato corrigido na tentativa seguinte;
- validação sempre reprovada: exatamente `max_tentativas` chamadas;
- agente sem `structured_response`;
- `recursion_limit` enviado na configuração da chamada;
- lote de tamanho 0;
- valores 0 nas variáveis de ambiente;
- timeout e retries HTTP no modelo.

Correção relacionada: quando uma etapa de implementação ou a base falhava, o
`metricas.json` não era gravado, e o custo das etapas já executadas se perdia.
Agora é gravado sempre (`try/finally`).

### Primeira execução real (módulo Users, 08/10/2026)

`SAIDA_DIR=saida/execucoes/20261008 python3 main.py --nome Users --arquivo exemplos/erp/modulos/users.md`
(log em `saida/execucoes/20261008.log`)

| Etapa | Resultado |
|---|---|
| Requisitos, Esclarecedor, Modelador, Arquiteto | aprovados na 1ª tentativa |
| Planejador da base | aprovado na 1ª tentativa |
| Implementador backend da base, lotes 1–5 | aprovados; o lote 3 precisou de 2 tentativas |
| Implementador backend da base, lote 6 | **reprovado nas 2 tentativas** → pipeline parou |

Foram 13 invocações no total e 25 arquivos de código gravados (todos já
validados). A parada mostra que os limites funcionam: o lote reprovado não foi
gravado e não houve novas tentativas além do limite.

**Causa da falha:** o Gemini escreveu o conteúdo correto de `OutboxPublisher`, mas
com o `path` de outro arquivo do lote (`OutboxProcessorService.java`). O resultado
foi um arquivo em duplicidade e outro faltando. O mesmo padrão aconteceu no lote 3
e foi corrigido no retry; no lote 6, o modelo repetiu o erro mesmo com a mensagem
de reprovação.

**Correção sugerida (não implementada):** reparo determinístico antes de validar.
Quando o tipo declarado num arquivo corresponde exatamente a um arquivo pedido e
não entregue, do mesmo pacote, o `path` seria corrigido em código. A alternativa
é reduzir `IMPL_LOTE`, para haver menos arquivos parecidos no mesmo lote.

Os próximos passos (Planejador do módulo, implementador do módulo e frontend) não
chegaram a rodar.

---

## Rodada 2 — Planejador e Implementadores (backend e frontend)

Data: 08/10/2026

### Resumo

O pipeline de cada módulo agora termina em **código**:

```
Requisitos → Esclarecedor → Modelador de dados → Arquiteto de software
  → Planejador → Implementador backend → Implementador frontend
```

- **Planejador** (agente novo): é a junção pedida entre o Modelador de dados e o
  Arquiteto de software. Recebe especificação, modelo de dados, arquitetura e a
  migration, e devolve um **plano de arquivos**. Para cada arquivo, o plano diz
  caminho, tipo, nomes exportados, dependências, tabelas, endpoints e requisitos.
  Também lista as telas do frontend.
- **Implementador backend** e **Implementador frontend** (agentes novos):
  escrevem os arquivos do plano **em lotes**. Cada lote é validado em código e
  só então gravado em disco.
- **Migration SQL gerada sem LLM** (`ddl.py`): o modelo de dados já é
  estruturado, então a tradução para `CREATE TABLE`/índices/FKs é
  determinística.
- **Base compartilhada**: um esqueleto de código gerado uma vez por projeto, no
  qual todos os módulos se encaixam. No backend, inclui build, ponto de entrada,
  tratamento de erros e segurança; no frontend, `package.json`, roteador, cliente
  HTTP e layout. É gerada pelos mesmos agentes, em modo `BASE`.
- **Retomar a partir de uma etapa** (`--de`): reaproveita os artefatos já
  gerados, para não pagar de novo pelas etapas de especificação.

São 39 testes novos, todos com agentes falsos e **nenhuma chamada à API do
Gemini**: 79 testes no total, todos passando. Os agentes novos **não foram
executados com o Gemini de verdade** nesta rodada.

### Por que saída estruturada, e não as tools de arquivo

Os Implementadores devolvem `GeneratedFiles` (lista de `{path, content}`), em vez
de usar as tools de `tools/manipulacao_de_arquivos.py`. Motivos:
- dá para **validar antes de gravar**: nada inválido chega ao disco;
- usa o mesmo mecanismo de nova tentativa das outras etapas;
- é testável com agentes falsos, sem um loop de chamadas de ferramenta;
- o código decide onde gravar e recusa caminhos fora da raiz.

As tools continuam no projeto, usadas só pela demo em `scripts/`.

### Onde cada arquivo pode morar (`layout.py`)

Essas regras valem para o Planejador, os Implementadores e os validadores:

| O quê | Caminho |
|---|---|
| Backend do módulo (JVM) | `src/main/java/<pacote do módulo>/` e `src/test/java/<pacote>/` |
| Backend do módulo (outras linguagens) | `src/<modulo>/` e `tests/<modulo>/` |
| Frontend do módulo | `src/modules/<modulo>/` |
| Rotas do módulo (importadas pela base) | `src/modules/<modulo>/routes.<ext>` |
| Migration do módulo | `src/main/resources/db/migration/V<nnn>__<modulo>.sql` (nnn = posição no build_order) |
| SQL da base (ex.: tabela de outbox) | `src/main/resources/db/migration/V0__*.sql` |

O arquivo de rotas com caminho fixo resolve um problema de ordem: o roteador da
base é gerado **antes** dos módulos, mas já pode importar as rotas de cada um.

### Validações em código

**Plano do módulo** (`validar_plano`):
- toda tabela do modelo tem um arquivo `entity`;
- todo endpoint da arquitetura tem um `controller`;
- todo serviço público e todo evento aparece em `exports`;
- tabelas, endpoints e requisitos citados existem;
- arquivos ficam dentro da pasta do módulo e não há migration planejada;
- `depends_on` aponta só para o plano ou para a base, sem ciclos;
- não replaneja arquivos da base;
- cada tela tem página planejada e um `api_client` que consome seus endpoints;
- existe o arquivo de rotas;
- se o contexto não tem frontend, `frontend_files` e `screens` ficam vazios.

**Plano da base** (`validar_plano_base`): nenhum arquivo em pasta de módulo, sem
tabelas, endpoints ou telas, SQL só com prefixo `V0__`, e `conventions`
preenchido. As convenções descrevem como os módulos devem registrar rotas, lançar
erros etc.

**Código gerado** (`validar_arquivos`, por lote):
- entregou exatamente os arquivos pedidos, sem vazios;
- não tem `TODO`, `FIXME` nem `// ...`;
- **Java/Kotlin:**
  - `package` bate com a pasta e o tipo principal tem o nome do arquivo;
  - imports do próprio projeto existem no plano, na base ou em módulos já
    gerados. Imports de bibliotecas (Spring etc.) não são checados;
  - `@Table(name=...)` só cita tabelas do modelo do módulo;
- **TS/JS:**
  - imports relativos e `@/` resolvem para arquivos conhecidos. Pacotes npm não
    são checados;
  - URLs `/api/vN/...` no código são endpoints da arquitetura do módulo ou de
    módulos já gerados. Parâmetros `${id}`, `{id}` e `:id` são equivalentes.

**Modelo de dados** (regra nova): restrições de coluna e de tabela precisam ser
SQL (`UNIQUE`, `CHECK`, `DEFAULT`, `CONSTRAINT ...`), já que viram DDL sem
revisão. As saídas reais do Gemini já seguiam esse formato.

### Lotes e contexto de cada chamada

- Os arquivos são agrupados em lotes de `IMPL_LOTE` (padrão 5), em ordem
  topológica de `depends_on`.
- Cada lote recebe:
  - o plano inteiro do lado (backend ou frontend), com os `exports` de todos os
    arquivos;
  - o **conteúdo já gerado** dos arquivos de que depende, até o limite de
    `IMPL_LIMITE_CONTEXTO` caracteres;
  - no backend, a DDL e o código das interfaces públicas de outros módulos que o
    módulo usa.
- No retry, os erros de validação são anexados à mensagem, como nas outras
  etapas.

### Arquivos novos e alterados

| Arquivo | O quê |
|---|---|
| `schemas.py` | `PlannedFile`, `Screen`, `ImplementationPlan`, `GeneratedFile(s)` |
| `system_prompts/system_prompts.py` | prompts `PLANEJADOR`, `IMPLEMENTADOR_BACKEND`, `IMPLEMENTADOR_FRONTEND` |
| `system_prompts/templates.py` | mensagens do planejador (módulo/base) e dos implementadores |
| `agents/planejador.py`, `agents/implementador_backend.py`, `agents/implementador_frontend.py` | agentes novos |
| `implementacao.py` (novo) | lotes, gravação segura, base, etapas 5–7 |
| `ddl.py` (novo) | modelo de dados → SQL |
| `layout.py` (novo) | regras de caminho, `slugify`, limpeza de cercas markdown |
| `execucao.py` (novo) | `executar_agente` e `PipelineError`, que saíram de `pipeline.py` para evitar import circular (`pipeline.py` reexporta os nomes antigos) |
| `validation.py` | validações do plano, da base e do código; restrições SQL no modelo |
| `pipeline.py` | etapas 5–7, `de` (retomar), base automática no modo módulo |
| `projeto.py` | base uma vez por projeto, código compartilhado, migrations numeradas, endpoints de outros módulos liberados para o frontend |
| `render.py` | plano e lista de arquivos no `relatorio.md` |
| `main.py` | `--de`; `--ate` agora vai até o frontend por padrão |
| `config.py` | `IMPL_LOTE`, `IMPL_LIMITE_CONTEXTO` |

### Testes sem chamar o Gemini

- `tests/fakes.py` tem os agentes falsos:
  - um **Planejador falso**, que devolve o plano da base ou o do módulo conforme
    a mensagem;
  - um **Implementador falso**, que lê da mensagem os arquivos pedidos e gera
    código mínimo **coerente com o plano** (pacote, imports das dependências,
    `@Table`, URLs de endpoint, exports). Com isso, os validadores reais são
    exercitados. Ele também pode “sabotar” respostas, para testar retry e falha.
- `tests/dados/modelo_users.json` é o modelo de dados real gerado pelo Gemini
  numa execução anterior. É usado para testar a DDL com dados reais.
- A DDL gerada é executada no **SQLite em memória** como checagem de sintaxe. Os
  `ALTER TABLE` de FK ficam de fora, porque o SQLite não os suporta.

Cobertura de `tests/test_implementacao.py`:
- DDL a partir do modelo de teste, de constraints/`WHERE`/`"null"` textual e da
  saída real;
- validação do plano: cada regra acima tem um caso;
- validação do plano da base;
- lotes respeitando dependências, e ciclo que não trava;
- código gerado: pacote, classe, imports (inclusive estático, aninhado e
  curinga), `@Table`, `TODO`, imports e URLs do frontend, endpoint de outro
  módulo, roteador importando rotas de módulos;
- pipeline completo gravando base, migration, backend e frontend;
- retry do implementador, falha definitiva (nada gravado do lote reprovado),
  falha na base e plano reprovado;
- `--de planejador` sem chamar as etapas 1–4, e erro quando faltam artefatos;
- `--ate planejador` não implementa;
- contexto sem frontend;
- base reaproveitada numa segunda execução;
- projeto com 3 módulos: base gerada uma vez, migrations `V001`–`V003`, rotas
  de todos os módulos.

```
python3 -m pytest -q   →   79 passed
```

### Custo

Cada lote é uma chamada. Com os artefatos reais do módulo Users, a mensagem do
Planejador tem cerca de 15 mil tokens, mais uns 4 mil de system prompt.
Recomendação para a primeira execução real:

```bash
python3 main.py --nome Users --arquivo exemplos/user.md --de planejador --ate planejador
```

O comando reaproveita `saida/users/` e revisa o plano antes de gastar com a
implementação. Atenção: os artefatos atuais de `saida/users/` foram gerados com
o prompt antigo; se o Planejador reclamar deles, regenere-os.

### Limitações / próximos passos

- **O código não é compilado nem testado.** As validações pegam erros
  estruturais (pacote, imports, tabelas, endpoints), mas não erros de tipo nem de
  lógica. O próximo passo natural é um ciclo de build (`mvn verify`,
  `npm run build`) com os erros devolvidos ao Implementador.
- Imports de bibliotecas externas não são checados contra o `pom.xml` e o
  `package.json`.
- No modo de um módulo, a base é gerada só para aquele módulo. No modo projeto,
  o roteador importa as rotas de todos os módulos do `build_order`; se um módulo
  falhar, o import dele fica quebrado.
- Rodar os agentes novos com o Gemini para calibrar os prompts. Isso não foi
  feito nesta rodada.

---

## Rodada 1 — Integração do Arquiteto-chefe e execução por projeto

Data: 08/10/2026

### Resumo

Antes, o sistema processava **um módulo por vez**. O PROJECT CONTEXT (a saída do
Arquiteto-chefe) estava **colado à mão** dentro dos prompts. Agora:

1. O **Arquiteto-chefe** faz parte do pipeline, tem saída estruturada e validação
   em código.
2. O contexto é **injetado** nos agentes, não fica fixo no código.
3. Existe um **modo projeto**: gera o contexto e processa todos os módulos na
   ordem de construção. Cada módulo recebe o que os anteriores já projetaram.
4. As validações usam as convenções **do contexto**, não constantes fixas do ERP
   de exemplo.
5. Ajustes menores: dependências, `.env.example`, script do chefe, README.

Os testes passaram de 24 para 40, sem chamar o LLM. Rodei o Arquiteto-chefe uma
vez com o Gemini: a saída passou na validação na 1ª tentativa (39 s, 3,6k tokens
de entrada / 4,9k de saída).

---

### 1. Arquiteto-chefe integrado

**Problema:** `agents/arquiteto_chefe.py` estava vazio. O script avulso pedia JSON
em texto livre, sem schema nem validação. O resultado foi copiado manualmente para
a constante `CONTEXTO` em `system_prompts.py`.

**O que mudou:**
- **`schemas.py`**: novo contrato `ProjectContext`, com stack, decisões
  transversais, convenções de nomes, comunicação entre módulos, mapa de módulos,
  `build_order`, ADRs, riscos e premissas. O `module_map` virou **lista** com campo
  `name`, em vez de dicionário com nomes como chave. Saída estruturada lida mal com
  chaves dinâmicas.
- **`agents/arquiteto_chefe.py`**: agente com `response_format=ProjectContext`.
- **`validation.validar_contexto`** reprova (e o agente tenta de novo, com os erros
  anexados) quando:
  - falta um módulo declarado, ou aparece um módulo de negócio inventado. Só
    módulos `platform` podem ser acrescentados;
  - um módulo depende de si mesmo ou de um módulo inexistente;
  - há **dependência circular**. A mensagem de erro mostra o ciclo;
  - o `build_order` está incompleto, repete módulos ou coloca um módulo antes de
    uma dependência dele;
  - há mais de 5 ADRs ou mais de 4 riscos, há risco de severidade `low`, ou os IDs
    ADRxx/ASxx estão fora do padrão.
- **`templates.msg_arquiteto_chefe`** anexa à descrição a lista de módulos
  declarados, para o agente usar os nomes exatos.
- **Prompt do chefe**: o formato do `module_map`, do `build_order` e dos ADRs foi
  alinhado ao schema.

### 2. Contexto injetado, não fixo

**Problema:** `CONTEXTO` (cerca de 320 linhas de JSON) era concatenado em todos os
prompts. Trocar de projeto exigia editar o código.

**O que mudou:**
- `system_prompts.py` agora contém **só as regras** de cada agente. A função
  `com_contexto(contexto, regras)` monta o system prompt com o contexto na frente,
  na mesma ordem de antes.
- Os agentes de módulo viraram `get_agente(contexto)`. O agente é reaproveitado
  enquanto o contexto for o mesmo (cache em `agents/_base.py`).
- O contexto antigo foi convertido para o novo formato e salvo em
  `exemplos/contexto_erp.json`. É o padrão quando um módulo roda sozinho
  (variável `CONTEXTO` em `config.py`). Por isso o modo de um módulo continua
  funcionando como antes.
- Trechos fixos do ERP de exemplo saíram dos prompts e dos schemas: "Java 21,
  Spring Boot 3", "PostgreSQL 16", `com.distributor.erp`, `/api/v1/` e
  "proibido Kafka". No lugar, os prompts remetem ao campo correspondente do
  contexto: `tech_stack`, `naming_conventions.packages`, `api_conventions`,
  `inter_module_communication`.
- A regra do `tenant_id` passou a ser condicional: só se aplica se
  `multi_tenancy.enabled` for verdadeiro.

### 3. Modo projeto (vários módulos)

**Problema:** cada execução processava um módulo. O contexto dos outros módulos
(`--outros`) era um texto escrito à mão.

**O que mudou:**
- **`projeto.py`** (novo):
  - `ler_modulos`: extrai os módulos da seção `MODULES` da descrição do sistema.
  - `gerar_contexto`: roda o chefe, valida e grava `0_contexto.json` e
    `contexto.md`.
  - `processar_projeto`: percorre o `build_order` e roda o pipeline de cada
    módulo. Uma falha num módulo não interrompe os seguintes; o status de cada um
    vai para `projeto.json`.
- **`templates.resumo_outros_modulos`** gera automaticamente o contexto dos outros
  módulos:
  - responsabilidade, dependências e capacidades de cada módulo, segundo o mapa
    do chefe;
  - para módulos já processados, os **serviços públicos e eventos já
    projetados**;
  - quais módulos dependem do módulo atual.

  O prompt do Arquiteto de Software agora pede para **reusar esses nomes** em vez
  de inventar novos.
- No modo de um módulo, quando `--outros` não é informado, esse resumo também é
  gerado a partir do contexto. Antes, o agente recebia "(no other module
  information provided)".
- **`main.py`** ganhou as opções `--projeto`, `--modulos-dir`, `--apenas`,
  `--so-contexto` e `--contexto`. Os comandos antigos continuam válidos.
- Exemplos: `exemplos/erp/sistema.md` (a descrição que estava dentro do script) e
  `exemplos/erp/modulos/users.md` (cópia de `exemplos/user.md`).

### 4. Validação guiada pelo contexto

**Problema:** `validation.py` tinha constantes do ERP de exemplo:
`PACOTE_BASE = "com.distributor.erp."`, PK UUID, `tenant_id` obrigatório e
prefixo `/api/v1/`.

**O que mudou:** a classe `Convencoes` e a função `convencoes(contexto)` derivam
essas regras do contexto:

| Regra | De onde vem |
|---|---|
| Pacote base | `naming_conventions.packages` (o que vem antes de `<module_name>`) |
| Colunas obrigatórias | `created_at`, `updated_at` e `tenant_id` se houver multi-tenancy |
| Tipo da PK | `UUID` se `data_conventions.id_type` contém "UUID"; senão, não checa o tipo |
| Prefixo da API | `/api/vN` encontrado em `api_conventions.versioning` |

Com o contexto de exemplo, o resultado é idêntico às constantes antigas; há um
teste que garante isso. Por isso os testes existentes não mudaram.

### 5. Ajustes menores

- `requirements.txt`: adicionado `langsmith`, que era importado em `pipeline.py`
  mas não estava listado.
- `.env.example` criado. O README mandava copiá-lo, mas ele não existia. Contém só
  nomes de variáveis, sem chaves.
- `scripts/arquiteto_chefe.py` agora usa o agente estruturado e grava em
  `saida/<projeto>/`. Equivale a `main.py --projeto ... --so-contexto`. As
  execuções antigas em `scripts/saidas/` foram mantidas.
- `scripts/demo_file_tools.py`: removido um import que não era usado.
- `pipeline.py`: removido código comentado. A função `carregar_contexto` foi
  adicionada.
- README atualizado.

### Testes

Novo arquivo `tests/test_projeto.py`, com 16 testes:
- leitura da seção `MODULES`, incluindo itens que continuam na linha seguinte;
- validação do contexto: o exemplo é válido; detecta módulo faltando, ciclo,
  `build_order` fora de ordem ou incompleto e dependência inexistente;
- convenções: o contexto de exemplo equivale às regras antigas; sem
  multi-tenancy e com outro pacote, as regras mudam;
- resumo dos outros módulos;
- `gerar_contexto` com nova tentativa após reprovação, e erro quando não há
  módulos;
- `processar_projeto` completo, falha isolada em um módulo e filtro `--apenas`.

```
python3 -m pytest -q   →   40 passed
```

### O que NÃO foi feito (próximos passos)

- **Consolidador**: checar a coerência *entre* módulos depois de gerados. Por
  exemplo, se o serviço que A diz usar de B existe na arquitetura de B. O resumo
  automático já reduz esse problema, mas não o verifica.
- **Revisão humana**: os `review_alerts` continuam apenas impressos e gravados em
  `projeto.json`. Não há pausa para aprovação.
- **Implementador / geração de código**: continua sendo a maior lacuna. As tools
  de arquivo estão prontas, mas nenhum agente gera código, DDL ou testes, e não há
  ciclo de compilação.
- **Execução paralela com LangGraph**: os módulos rodam em sequência. Módulos sem
  dependência entre si poderiam rodar em paralelo.
- **Avaliação**: ainda há só um exemplo de entrada.
- **Não mexi** em `sistemas.zip` (backup antigo) nem em `saida/users/3_projeto.json`
  (sobra do antigo agente "projetista"). Podem ser apagados, se não forem mais
  necessários.
- A descrição de exemplo mantém o erro de digitação "Sringboot" do original, para
  não mudar a entrada dos experimentos já feitos.
