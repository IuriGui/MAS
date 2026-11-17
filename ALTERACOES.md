# Alterações

- [Relato 9. Redução de custo por lote: recorte da mensagem, verificação proporcional e medição](#relato-9-redução-de-custo-por-lote-recorte-da-mensagem-verificação-proporcional-e-medição)
- [Relato 8. Execução com sandbox: quatro erros documentados, não corrigidos](#relato-8-execução-com-sandbox-quatro-erros-documentados-não-corrigidos)
- [Relato 7. Organização dos arquivos](#relato-7-organização-dos-arquivos)
- [Relato 6. Implementadores no sandbox Docker](#relato-6-implementadores-no-sandbox-docker)
- [Relato 5. Regeneração do módulo e aplicação no ar](#relato-5-regeneração-do-módulo-e-aplicação-no-ar)
- [Relato 4. Execução completa, compilação do código gerado e correções](#relato-4-execução-completa-compilação-do-código-gerado-e-correções)
- [Relato 3. Limites contra repetição sem fim e primeira execução real](#relato-3-limites-contra-repetição-sem-fim-e-primeira-execução-real)
- [Relato 2. Planejador e Implementadores (backend e frontend)](#relato-2-planejador-e-implementadores-backend-e-frontend)
- [Relato 1. Integração do Arquiteto-chefe e execução por projeto](#relato-1-integração-do-arquiteto-chefe-e-execução-por-projeto)

---

## Relato 9. Redução de custo por lote: recorte da mensagem, verificação proporcional e medição

Data: 16/11/2026. Branch `deep-agents`.

### Por quê

Feito depois de constatado que o gasto continuava alto mesmo com as
dependências só em assinaturas (relato 8). Nos 3 primeiros lotes do backend
do Customers:

| Lote | Conteúdo | Entrada | Saída |
|---|---|---|---|
| 1 | 5 enums (~400 bytes cada) | 0,76 mi | 2,8 mil |
| 2 | 3 entidades JPA + validador de CPF/CNPJ | 3,58 mi | 31 mil |
| 3 | 5 eventos simples | 1,16 mi | — |

Reconstruindo sem LLM o que o agente recebia no lote 3, cada passo levava:

| Parte | Caracteres |
|---|---|
| System prompt (PROJECT CONTEXT + regras) | 18.430 |
| Descrição das 8 ferramentas do deepagents | 10.527 |
| Mensagem do lote | 154.239 |

Da mensagem, só 1–2% era específico do lote: os 5 arquivos a gerar. O resto
era o seguinte:

| Parte da mensagem | Caracteres | % |
|---|---|---|
| Especificação final inteira | 39.712 | 26% |
| Arquitetura inteira | 31.114 | 20% |
| Plano dos 60 arquivos do módulo | 44.098 | 29% |
| API da base (assinaturas de toda a base) | 21.813 | 14% |
| Resumo da base | 6.136 | 4% |
| Migration | 4.883 | 3% |

Isso dá uns 46 mil tokens de parte fixa por passo, reenviados em cada um dos
~15–60 passos de um lote. Além disso, o agente rodava `mvn clean test-compile`
e a suíte inteira (75 testes, cada um subindo o Spring) até em lote de enums.
Os logs inteiros entravam no histórico.

As propostas foram listadas por plano (A–E) e escolhidas pelo usuário: A1–A6,
B1, B2, B5 e a medição.

### Mudanças

**A1–A5. Recorte da mensagem do lote** (`mas/agentes/mensagens.py`, só no
sandbox; sem sandbox o agente não lê arquivos e continua com a mensagem
inteira):

| | Antes | Agora |
|---|---|---|
| A1. Plano | os arquivos do módulo em JSON | os do lote completos; os demais uma linha cada (`caminho [tipo]: exports`) |
| A2. Especificação | inteira | escopo, requisitos citados pelos arquivos do lote e as decisões (`resolved_problems`) que os afetam |
| A3. Arquitetura | inteira | pacotes; endpoints citados pelos arquivos; serviços públicos, eventos e dependências só para os tipos de arquivo que os usam; decisões dos requisitos citados |
| A4. Migration | sempre | só em lotes com entidade, repositório, mapper, teste ou tabela |
| A5. API da base | assinaturas de toda a base | sai; os arquivos da base que o lote usa já vêm nas dependências (o Planejador os lista em `depends_on`) |

Frontend: requisitos, endpoints e telas só os do lote, mais o resto do plano
em uma linha por arquivo.

**A6. Documentos em disco** (`implementacao.gravar_documentos`): antes do 1º
lote, o pipeline grava em `<código>/.mas/<módulo>/` a especificação, a
arquitetura e o plano inteiros, a migration e a API completa da base. A
mensagem lista os caminhos no container, e o prompt (`MODO_SANDBOX`) diz para
lê-los só se faltar algo. Os arquivos existem antes da foto de cada lote, e
por isso a `ColetaSandbox` não os apaga.

**B1. Verificação proporcional** (`mensagens.verificacao_do_lote`): cada lote
recebe o comando exato de verificação.
- Lote sem teste: `mvn -q compile 2>&1 | tail -n 40`.
- Lote com teste: `mvn -q test -Dtest=<classes do lote> 2>&1 | tail -n 60`.
- Frontend: `npx tsc --noEmit 2>&1 | tail -n 40`.
- `FEATURE TEST: yes` (subir a aplicação e testar) só para lotes com controller
  ou página/rotas. Nos demais, o prompt proíbe subir a aplicação.

O prompt proíbe a suíte inteira e o `mvn clean`. Quem roda a suíte agora é o
pipeline, uma vez, no fim do backend do módulo (`mvn -q test`), e o build do
frontend (`npm run build`). O resultado fica no manifesto
(`verificacao_final`) e no `relatorio.md`, e não interrompe a execução.

**B2. Saída curta:** o prompt explica que a saída das ferramentas fica na
conversa e é reenviada a cada passo. Pede `2>&1 | tail -n 60` (ou um grep dos
erros) em builds, testes e logs, e que o agente leia só os arquivos de que
precisa.

**B5. Teto de passos:** `LIMITE_PASSOS_SANDBOX` caiu de 250 para 120 passos do
grafo (cada ciclo modelo → ferramenta é mais de um passo). Ao estourar, o que
foi gravado é colhido e validado como antes.

**Limite de lotes por execução:** `LOTES_POR_EXECUCAO` (0 = sem limite) para a
etapa depois de N lotes aprovados, com o progresso salvo. Serve para medir
poucos lotes sem gastar o módulo inteiro.

**Medição** (`mas/pipeline/execucao.py`): cada chamada registra, além dos
tokens:
- chamadas ao modelo (passos), chamadas de ferramenta e quantas de cada
  ferramenta;
- tamanho total e maior saída de ferramenta;
- tokens lidos de cache, quando o provedor informa (`input_token_details`).

Os números são somados na etapa (`metricas.json`) e guardados por lote no
manifesto. O `relatorio.md` mostra tudo por lote, e o `metricas.md` ganhou a
coluna "Chamadas ao modelo".

Também foi corrigida uma perda de dados: quando o limite de passos estourava,
os tokens daquela chamada não eram contados. Agora o `AgenteSandbox` acompanha
o grafo por `stream` e, ao estourar, devolve o último estado.

### Medido sem LLM

Tamanho médio da mensagem por lote, em caracteres:

| Módulo | Backend | Frontend |
|---|---|---|
| Users | 154.274 → 45.785 (30%) | 99.996 → 23.162 (23%) |
| Catalog | 202.733 → 60.376 (30%) | 136.760 → 32.715 (24%) |
| Customers | 168.417 → 49.453 (29%) | 52.535 → 22.643 (43%) |

Nos lotes 4–12 do backend do Customers, a mensagem passa de 150–236 mil para
35–87 mil caracteres.

Testes: 6 novos (123 no total).

### Resultado com LLM

Lotes 4 a 6 do backend do Customers, em 17/11/2026, com
`LOTES_POR_EXECUCAO=3`. Os três foram aprovados de primeira.

| Lote | Conteúdo | Entrada | Em cache | Saída | Chamadas ao modelo | Tempo |
|---|---|---|---|---|---|---|
| 1* | 5 enums | 0,76 mi | ? | 2,8 mil | ? | 1,5 min |
| 2* | 3 entidades + validador | 3,58 mi | ? | 31 mil | ? | 6,8 min |
| 3* | 5 eventos | 1,16 mi | ? | 15 mil | ? | 2,7 min |
| 4 | 3 eventos + 2 repositórios | 1,47 mi | 1,35 mi (92%) | 19 mil | 39 | 4,1 min |
| 5 | 1 repositório + 4 DTOs | 1,14 mi | 1,03 mi (90%) | 13 mil | 35 | 3,0 min |
| 6 | 2 DTOs + 3 interfaces públicas | 2,01 mi | 1,86 mi (93%) | 16 mil | 48 | 4,9 min |

\* Antes deste relato, com a mensagem inteira e sem medição de passos e cache.

**Média por lote:** 1,83 mi (lotes 1–3) → 1,54 mi (lotes 4–6), **16% a
menos**. A comparação é aproximada: os lotes têm conteúdos diferentes, e o
lote 2 pesa muito na média antiga.

**O que funcionou:**
- **B1:** o agente rodou só `mvn -q compile` (e `test-compile`), sem a suíte e
  sem subir a aplicação. Foram 3 `execute` por lote.
- **B2:** os comandos saíram com `| tail -n 40`.

**O que comeu a economia: leituras.** Nos três lotes, as 119 chamadas de
ferramenta foram:

| `read_file` | `grep` | `glob` | `write_file` | `execute` | `ls` |
|---|---|---|---|---|---|
| 54 | 25 | 15 | 15 | 9 | 1 |

São 18 leituras por lote para 5 arquivos escritos. As saídas das ferramentas
somaram 246 mil caracteres (a maior, 20,5 mil), e cada uma fica no histórico e
é reenviada nos passos seguintes. Com a parte fixa em ~18 mil tokens, a média
de ~37 mil tokens por chamada mostra que o histórico virou a maior parte do
contexto. A medição não registra quais arquivos foram lidos, então não se
sabe se foram os documentos de `.mas/` (a especificação inteira tem 40 mil
caracteres) ou o código já gerado, para seguir o padrão.

**Cache:** 91–93% da entrada veio do cache do provedor, ou seja, do começo
repetido da conversa. Se o cache tiver desconto, o custo em reais é bem menor
do que o total de tokens indica. O desconto do `gemini-3.8-flash` não foi
verificado.

**Registro do que o agente lê** (feito depois desta execução,
`execucao._alvo`): cada chamada de `read_file` (caminho, e o trecho quando há
`offset`), `grep`, `glob` (padrão e pasta), `ls` e `execute` (o comando) é
registrada com o número de vezes e os caracteres devolvidos. Os números ficam
em `leituras`, na etapa (`metricas.json`) e em cada lote (manifesto). O
`relatorio.md` lista, por lote, as 15 maiores. Escritas ficam de fora, porque
o conteúdo escrito já conta como saída do modelo. Só vale para as próximas
execuções: o histórico dos lotes 4–6 não foi guardado.

**Próximos passos possíveis:**
1. Rodar 2 ou 3 lotes com o registro, para saber o que o agente procura.
2. Pôr na mensagem um exemplo curto de um arquivo do mesmo tipo já gerado,
   para evitar a exploração.
3. Limpar do histórico as saídas antigas de ferramentas (C1).
4. Lotes simples (DTO, evento, enum, interface) sem sandbox (E1).

---

## Relato 8. Execução com sandbox: quatro erros documentados, não corrigidos

Data: 16/11/2026. Commit `c00c820`, módulo Users, a partir do backup `impl_f3`.
Este relato registra os erros e as correções a fazer; nada foi alterado no
código gerado. No pipeline, só o relatório mudou (ver "Onde ver o relato dos
agentes").

### Execução

| | Lotes | Tentativas | Tokens (entrada / saída) | Tempo |
|---|---|---|---|---|
| Base do backend | 4 | 4 | 5,40 mi / 101 mil | 20 min |
| Base do frontend | 4 | 4 | 4,11 mi / 55 mil | 11 min |
| Backend de Users | 11 | 11 | 16,58 mi / 178 mil | 50 min |
| Frontend de Users | 3 | 3 | 7,31 mi / 87 mil | 17 min |
| **Total** | 22 | 22 | **33,4 mi / 421 mil** | 98 min |

Os números da base do backend (5.395.916 / 100.991 tokens, 1.187 s) foram lidos
do `base/metricas.json` antes de a execução da base do frontend sobrescrevê-lo;
hoje o arquivo só tem a base do frontend.

O que funciona (conferido fora do pipeline):
- o backend compila (`mvn test-compile`) e os 30 testes passam com PostgreSQL;
- a aplicação sobe (com a configuração gerada, ver abaixo);
- as chamadas das telas do frontend batem com os endpoints do backend (conferido
  lendo o código: a aplicação do frontend não abre, ver erro 2);
- com o seed de teste (`teste_local/`), o login e as listagens de usuários e
  papéis funcionam pela API.

Para testar à mão: `docker compose -f teste_local/compose.yml up` sobe banco,
backend, frontend e um administrador de teste (`teste_local/seed_admin.sql`),
sem alterar o código em `saida/`.

### Erro 1: a aplicação não usa as migrations

A base gravou no `application.yml`:

```yaml
spring:
  jpa:
    hibernate:
      ddl-auto: update      # o Hibernate cria/altera as tabelas
  flyway:
    enabled: false          # V001__users.sql nunca é aplicada
```

Com isso, o schema gerado a partir do modelo de dados (`mas/codigo/ddl.py`) é
ignorado, e as tabelas saem das entidades JPA. Ligando o Flyway com o Hibernate
só validando (`--spring.flyway.enabled=true --spring.jpa.hibernate.ddl-auto=validate`),
a aplicação não sobe:

```
Schema-validation: missing column [deleted_at] in table [password_reset_tokens]
```

**Causa:** a `BaseEntity` (`common/entity/BaseEntity.java`, `@MappedSuperclass`
herdada pelas 7 entidades de Users) mapeia seis colunas para toda tabela:

| Coluna | Na `BaseEntity` | Na migration |
|---|---|---|
| `id`, `tenant_id`, `created_at`, `updated_at` | sim | em todas as 7 tabelas |
| `deleted_at` | sim | só em `users` e `roles` |
| `version` (`@Version`) | sim | em nenhuma |

O Planejador e o Implementador da base não sabem quais colunas *toda* tabela
tem: a base é gerada antes dos módulos, e as convenções do PROJECT CONTEXT
citam soft delete e versionamento sem dizer onde. O relato do lote 2 da base
diz isso abertamente ("`BaseEntity.java`: MappedSuperclass providing […]
deleted_at (soft-delete), and optimistic locking version counter").

Os dois erros vieram de lotes diferentes. O `application.yml` foi escrito no
lote 1 da base, já com Flyway desligado e `ddl-auto: update`, antes de a
`BaseEntity` existir (lote 2). Como nenhum arquivo fora do lote é mantido, ele
não foi alterado depois. O relato do lote 1 não menciona a escolha, e o
histórico do agente não foi guardado (LangSmith desligado), então o motivo não
é conhecido. Na base anterior (backup `impl_f3`), o mesmo arquivo tinha
`ddl-auto: validate` e Flyway ligado. Com o Hibernate criando o schema, o
desencontro da `BaseEntity` nunca apareceu: build e testes passam porque o
schema bate com as entidades, e nenhuma validação em código reprova o Flyway
desligado.

É o mesmo desencontro do relato 5 (correção manual nº 1, migration `V900`).
Naquela vez ele foi corrigido à mão no código; a base foi regenerada nesta
execução e o erro voltou.

### Correção do erro 1 (não aplicada)

**No código gerado.** Já provada numa cópia do backend, fora de `saida/`:
compila, sobe com Flyway ligado e `ddl-auto=validate` num banco limpo, e os 30
testes passam.

1. `BaseEntity`: deixar só `id`, `tenant_id`, `created_at` e `updated_at`;
   remover `deletedAt`, `version` e os métodos `isDeleted`, `markDeleted` e
   `restore`.
2. `UserEntity` e `RoleEntity`: receber `deleted_at` com
   `getDeletedAt`/`setDeletedAt`, `isDeleted`, `markDeleted` e `restore`, que
   são os usados pelos serviços, repositórios e testes de Users.
3. `application.yml`: `flyway.enabled: true` e `ddl-auto: validate`.

**No pipeline.** Para o erro não voltar na próxima geração:

1. **Contrato de colunas comuns:** a mensagem do Planejador e do Implementador da
   base informa as colunas que toda tabela tem (`id` mais as `colunas_base` das
   convenções). A `@MappedSuperclass` mapeia só essas, e soft delete,
   versionamento e afins ficam na entidade cuja tabela tem a coluna.
2. **Validação da classe base:** reprovar `@MappedSuperclass` que mapeie coluna
   fora do contrato, lendo `@Column(name)` ou convertendo o nome do campo para
   snake_case e ignorando estáticos e `@Transient`.
3. **Schema só pelas migrations:**
   - no prompt, proibir desligar Flyway/Liquibase ou usar `ddl-auto` diferente de
     `validate`/`none`;
   - na validação, reprovar `ddl-auto` `update`/`create`/`create-drop` e
     `flyway|liquibase.enabled: false` em YAML, `.properties` ou código, como
     `@SpringBootTest(properties=...)`.
4. **Verificação final:** depois do backend de cada módulo, empacotar e subir a
   aplicação com Flyway ligado e `ddl-auto=validate` num banco limpo. Registrar o
   resultado no `6_backend.json`.

Os quatro itens foram implementados e testados sem LLM na branch
`contexto-enxuto` (commit `f6b89a8`), que ficou fora da linha principal junto
com outras mudanças de custo que não deram resultado. Não chegaram a rodar com
o Gemini.

### Erro 2: o frontend não abre

O Vite serve o `index.html`, mas a aplicação quebra ao carregar:

```
[vite] Internal server error: Failed to resolve import "../modules/catalog/routes"
from "src/router/AppRoutes.tsx". Does the file exist?
```

**Causa:** o `src/router/AppRoutes.tsx` da base (lote 4) importa
estaticamente as rotas dos 8 módulos do ERP (`users`, `catalog`, `customers`,
`inventory`, `audit`, `purchasing`, `sales`, `finance`). Só `users` existe. O
plano citava `users`, `catalog` e `inventory`, e o agente ampliou para os 8. A
`Sidebar.tsx` (lote 3) também lista os 8 módulos, mas como links fixos, sem
import; ela compila e leva à página 404 nos módulos que faltam.

A instrução de importar cada módulo vem do próprio prompt do Planejador da
base (`mas/agentes/prompts.py`, regras do BASE PLAN): "The router imports
every module's routes file at the paths given in the message". O agente fez
o que foi pedido.

Os dois agentes viram o erro e o ignoraram, como o prompt permite. Os resumos
estão no campo `notas` dos manifestos:

- **Base, lote 4** (`base/7_frontend.json`): "The only reported compilation
  errors are TS2307 missing module declarations for module route files
  (`../modules/*/routes`) that are planned for subsequent batches and have not
  yet been generated, as expected by the batch verification rules. Validated
  that upon creating mock route declarations for the 8 planned modules,
  `npx tsc --noEmit` succeeds with exit code 0." Os stubs foram criados fora do
  lote e apagados pela `ColetaSandbox`, então o import ficou quebrado.
- **Users, lote 3** (`users/7_frontend.json`): "The only outstanding compiler
  errors are external imports in `src/router/AppRoutes.tsx` for modules not yet
  created […]. These will resolve automatically as subsequent modules are
  generated." Para testar o Vite, o agente pediu só os próprios arquivos
  (`UserProfilePage.tsx`, `routes.tsx`) e nunca carregou a aplicação a partir do
  `main.tsx`.

Por que passou:

1. **Regra do prompt:** `MODO_SANDBOX` (`mas/agentes/prompts.py`) diz "errors
   caused only by files that are planned but not generated yet (later batches
   or modules) are expected: ignore them". O "or modules" cobre qualquer módulo
   do projeto, inclusive os que não serão gerados nesta execução.
2. **Base amarrada a todos os módulos:** com imports estáticos, o frontend só
   abre quando os 8 módulos existirem. No backend isso não acontece, porque o
   Spring encontra os controllers pelo component scan.
3. **Nenhuma verificação do frontend inteiro:** a validação em código não roda
   `tsc` nem o build no fim do módulo, e nenhum agente carregou a aplicação.

### Correção do erro 2 (não aplicada)

**No código gerado.** Basta o `AppRoutes.tsx`, porque o `modules/users/routes.tsx`
já exporta `RouteObject[]` como `default` e é compatível com descoberta:

```ts
const modulos = import.meta.glob<{ default?: RouteObject[] }>(
  '../modules/*/routes.tsx', { eager: true });
const allModuleRoutes = Object.values(modulos).flatMap((m) => m.default ?? []);
```

Opcional: a `Sidebar` mostrar só os módulos que existem, pelo mesmo mecanismo.

**No pipeline:**

1. **Descoberta em vez de import:** o plano e o prompt da base pedem que o
   roteador e o menu juntem os módulos por `import.meta.glob` (cada módulo
   exporta `routes.tsx` e, para o menu, um `nav.ts`). A base não importa nenhum
   módulo pelo nome, e um módulo novo aparece sem editar arquivo da base.
2. **Regra do prompt:** trocar "later batches or modules" por "later batches of
   this same module". Um erro em arquivo da base ou de outro módulo passa a ser
   reportado, não ignorado.
3. **Verificação final do frontend:** depois do frontend de cada módulo, rodar
   `npm run build` (`tsc` mais o build do Vite a partir do `main.tsx`) e
   registrar o resultado no `7_frontend.json`, como a verificação final do
   backend.

Criar arquivos vazios de rota para cada módulo na base faria a aplicação abrir,
mas o módulo teria de sobrescrever um arquivo da base, o que contraria a regra
de não mexer fora do lote, e o menu continuaria com links para módulos que não
existem.

### Erro 3: o bloqueio por tentativas de login não funciona (FR03)

O requisito pede bloquear a conta por 15 minutos depois de 5 senhas erradas. O
agente do lote 11 do backend de Users avisou no relato, como problema fora do
lote:

> In `AuthenticationService.java` (from batch 10/11), `login(...)` is annotated
> with `@Transactional` without specifying `noRollbackFor = {BadCredentialsException.class,
> LockedException.class}`. Consequently, throwing `BadCredentialsException` or
> `LockedException` on failed credentials rolls back the database transaction
> […], discarding the updated `failed_login_attempts` counter.

Confirmado com a aplicação no ar (`teste_local/`): depois de 6 logins com senha
errada, `failed_login_attempts` continua 0, o status continua `ACTIVE`, e o
login com a senha certa entra. O teste de bloqueio gerado
(`AuthenticationIntegrationTest`) parte de um usuário já `LOCKED`, sem errar a
senha cinco vezes, e por isso passa.

**Por que passou:** o prompt manda o agente reportar o problema fora do lote na
mensagem final, e ele reportou, mas o pipeline só guarda essa mensagem no
manifesto. Ninguém lê o aviso nem reabre o lote 10. Além disso, o lote 10, que
gerou o `AuthenticationService`, terminou sem relato nenhum.

### Correção do erro 3 (não aplicada)

**No código gerado:** em `AuthenticationService.login`, gravar o contador
numa transação própria (`Propagation.REQUIRES_NEW`) ou usar
`@Transactional(noRollbackFor = {BadCredentialsException.class, LockedException.class})`.
Acrescentar um teste que erra a senha cinco vezes e espera 423.

**No pipeline:**

1. **Problemas fora do lote como dado, não texto:** o agente devolve os
   problemas que achou fora do lote numa lista separada (arquivo e descrição),
   e o pipeline os junta no manifesto.
2. **Destino desses problemas:** um problema em arquivo de lote anterior do
   mesmo módulo reabre aquele lote com o aviso na mensagem. Os demais vão para
   os alertas de revisão humana, impressos no fim da execução junto com os
   alertas da especificação.
3. **Lote sem relato:** registrar como alerta (hoje só aparece no relatório).

### Erro 4: a redefinição de senha não fecha (FR06)

`POST /api/v1/auth/forgot-password` gera o token, grava só o hash em
`password_reset_tokens` e descarta o token original no fim do método
(`AuthenticationService.forgotPassword`). Ele não vai para e-mail, log, evento
nem resposta HTTP. `POST /api/v1/auth/reset-password` exige esse token
original (`reset_token`). O endpoint existe, mas ninguém consegue concluir uma
redefinição.

O teste gerado (`AuthenticationIntegrationTest.testResetPassword_SuccessAndSingleUseEnforcement`)
cria o token no próprio teste e grava o hash direto no repositório, sem passar
pelo `forgot-password`, e por isso passa. O outro teste do fluxo só confere que
`forgot-password` responde 200.

**Causa:** a lacuna começa na especificação. O FR06 pede "a cryptographically
secure, single-use reset token valid for 15 minutes" e a especificação
consolidada descreve geração, hash e validade, mas não diz por onde o token
chega ao usuário. A lista de eventos do módulo não tem um evento de pedido de
redefinição, e notificação (e-mail) não está no escopo de nenhum módulo do
ERP. O Implementador seguiu a especificação ao pé da letra, e nenhum agente
apontou a lacuna. Não está nos alertas de revisão humana nem nos relatos dos
lotes.

### Correção do erro 4 (não aplicada)

**No código gerado:** publicar um evento `PasswordResetRequestedEvent` com o
token, para um módulo de notificação consumir, e, enquanto esse módulo não
existir, registrar o token no log só no perfil de desenvolvimento. Trocar o
teste por um que chama `forgot-password`, captura o evento e usa o token em
`reset-password`.

**No pipeline:**

1. **Especificação:** o Esclarecedor verifica, para cada fluxo que entrega algo
   ao usuário fora da API (token, convite, senha temporária), por qual canal
   isso chega. Sem canal no escopo, vira alerta de revisão humana.
2. **Testes pelo fluxo público:** o prompt do Implementador pede que os testes
   de integração de um fluxo usem os endpoints do próprio fluxo, sem gravar
   estado intermediário direto no repositório.

### Onde ver o relato dos agentes

Cada Implementador termina o lote com uma mensagem: o que gerou, o que
verificou e os problemas que achou. O pipeline guarda essa mensagem no campo
`notas` do manifesto (`6_backend.json`, `7_frontend.json`). Antes, o relatório
só a mostrava como uma lista corrida, só do frontend quando a execução era
retomada com `--de implementador_frontend`, e nunca para a base.

Mudança feita (`mas/relatorio.py`, `mas/pipeline/modulo.py`,
`mas/pipeline/implementacao.py`):

- o `relatorio.md` de cada módulo tem, em "Código backend" e "Código
  frontend", a seção **Relato do implementador, por lote**, com os arquivos e
  a mensagem de cada lote, e destaca os lotes sem relato;
- na retomada, o relatório inclui o código das etapas que não rodaram de novo;
- a base ganha o próprio `base/relatorio.md` (plano mais relatos).

Testes: 2 novos em `tests/test_implementacao.py` (111 no total). Os relatórios
desta execução foram regenerados a partir dos artefatos, sem LLM:
`saida/sistema/users/relatorio.md` e `saida/sistema/base/relatorio.md`.

### Custo: dependências só com assinaturas, e Lombok

Feito depois de constatado o gasto elevado de tokens nos dois primeiros
módulos (implementação, entrada / saída):

| | Backend | Frontend |
|---|---|---|
| Users | 16,58 mi / 178 mil (11 lotes, 1,5 mi por lote) | 7,31 mi / 87 mil (3 lotes) |
| Catalog | 33,11 mi / 328 mil (10 lotes, 3,3 mi por lote) | 7,55 mi / 140 mil (4 lotes) |

A entrada fica 50 a 100 vezes maior que a saída porque o deep agent reenvia a
conversa inteira a cada passo, inclusive a mensagem do lote. Nessa mensagem ia
o código inteiro de cada arquivo de que o lote depende (`depends_on`), até 400
mil caracteres. O Catalog depende de mais arquivos que o Users. Somando os
lotes, mandou 583 mil caracteres de dependências, e o lote mais pesado levou
193 mil caracteres (~48 mil tokens), reenviados a cada passo.

O Implementador não precisa do corpo dos métodos das dependências, só do que
cada método faz e do que recebe e devolve. No sandbox ele ainda pode ler
qualquer arquivo com `read_file`.

**Mudanças:**

1. **Dependências só com assinaturas no sandbox**
   (`implementacao.conteudo_dependencias(so_assinaturas=True)`): cada arquivo
   do `depends_on` vai com pacote, tipo, construtores, métodos e campos
   públicos, sem corpo, e cada método com a 1ª frase do seu Javadoc. O prompt
   (`MODO_SANDBOX`) diz que isso basta para chamar o código e que o arquivo
   inteiro só deve ser lido quando a implementação for necessária. Fora do
   sandbox (`SANDBOX=0`), o agente não tem como ler arquivos, e continua
   recebendo o código inteiro.
2. **`assinaturas.py`:**
   - acrescenta a 1ª frase do Javadoc de cada método;
   - gera as assinaturas que o Lombok cria (`@Getter`, `@Setter`, `@Data`,
     `@Value` na classe ou no campo, `@NoArgsConstructor`,
     `@AllArgsConstructor`, `@RequiredArgsConstructor` e `@Builder`), para a
     API continuar aparecendo inteira;
   - deixa de mostrar anotações de campos privados soltas.
3. **Lombok** (`mas/agentes/prompts.py`), em dois prompts:
   - `IMPLEMENTADOR_BACKEND` (lotes da base e dos módulos), regra nova depois
     da de mapeamento da persistência:
     ```
     - Java with Lombok in the build file: do not hand-write boilerplate that Lombok
       generates (getters, setters, constructors, builders, equals/hashCode,
       toString). JPA entities: @Getter, @Setter and @NoArgsConstructor (plus
       constructors with logic, if any); never @Data, @EqualsAndHashCode or
       @ToString on entities. DTOs, events and exceptions: Lombok (@Getter,
       @Setter, @NoArgsConstructor, @AllArgsConstructor, @Builder as needed) or
       Java records. Services and controllers: @RequiredArgsConstructor with final
       fields instead of a hand-written constructor.
     ```
     `@Data`, `@EqualsAndHashCode` e `@ToString` ficam proibidos em entidades
     porque o `equals`/`hashCode`/`toString` gerado percorre relacionamentos
     lazy.
   - `PLANEJADOR`, regras do BASE PLAN: o item do backend passou de "build and
     configuration files" para "build and configuration files (for Java,
     include Lombok with its annotation processor configured)". Só tem efeito
     se a base for gerada de novo.

   O `pom.xml` desta execução já tem o Lombok com o processador de anotações,
   só não era usado. A base, Users e Catalog continuam sem Lombok, e só os
   módulos seguintes o usam.

**Medido sem LLM** (dependências somadas de todos os lotes, em caracteres):

| | Antes | Só assinaturas |
|---|---|---|
| Users, backend | 360.602 | 124.856 (35%) |
| Catalog, backend | 583.261 | 138.249 (24%) |
| Users, frontend | 176.862 | 9.880 (6%) |
| Catalog, frontend | 317.774 | 31.467 (10%) |

Getters, setters, `equals`/`hashCode`/`toString` e construtores triviais eram
19% do código gerado do backend de Users e 14% do de Catalog, e 23 a 26% das
dependências enviadas. O Lombok reduz o que o agente escreve e o que entra no
histórico; com as dependências já só em assinaturas, o efeito dele na entrada
é menor.

**A comparar:** o gasto por lote dos próximos módulos (agora no `relatorio.md`
e em `metricas.md`) contra Users e Catalog. O risco é o agente ler mais
arquivos, ou chamar a API errada por não ver o corpo, e devolver parte da
economia. Isso aparece no número de tentativas e nos relatos dos lotes.

Testes: 3 novos (117 no total).

### Métricas do projeto

Mudança feita (`mas/metricas.py`, `mas/pipeline/projeto.py`,
`mas/pipeline/implementacao.py`):

- `saida/<projeto>/metricas.md` e `metricas_projeto.json`: tokens de entrada e
  de saída, tempo, tentativas e lotes no total, por módulo (Arquiteto-chefe,
  base e cada módulo) e por parte (especificação, planejamento, backend,
  frontend). São regravados depois da base e de cada módulo, e
  `python -m mas.metricas saida/<projeto>` os regenera a partir dos
  `metricas.json`. O tempo é o das chamadas aos agentes, incluindo o que eles
  rodam no sandbox.
- Por lote: o manifesto registra tokens, tempo e tentativas de cada lote, e o
  `relatorio.md` mostra esse gasto ao lado do relato do agente.
- `metricas.json` é gravado a cada lote, e não só no fim do módulo.
- Três perdas de dados corrigidas:
  - a base recomeçava as métricas do zero a cada execução e sobrescrevia o
    arquivo. Na execução do Catalog, o `base/metricas.json` virou `{}` e foi
    restaurado à mão com os números acima;
  - o `projeto.json` só guardava os módulos da execução atual, e um
    `--apenas Catalog` apagava o status de Users;
  - na retomada, os lotes recomeçavam do 1 e repetiam números no manifesto.
    Agora continuam a numeração ("batch 2/2").

As etapas reaproveitadas do backup `impl_f3` também entram:
- Arquiteto-chefe (`metricas_contexto.json`);
- especificação (etapas 1–4) dos 8 módulos;
- Planejador de Users, Catalog e Inventory.

Os artefatos em `saida/sistema` são idênticos aos do backup. Os números
ficam em `<modulo>/metricas_backup.json`, separados dos da execução atual, e
o consolidado soma os dois. A implementação antiga do backup não entra,
porque aquele código foi refeito. O Planejador da base não tem métricas no
backup.

Testes: 3 novos (114 no total).

### Outras pendências desta execução

- **Administrador inicial:** o pipeline não gera seed, então ninguém consegue
  entrar na aplicação gerada (mesma lacuna do relato 5, nº 2). Para teste à mão
  há o `teste_local/seed_admin.sql`, fora do código gerado.
- **Custo:** o deep agent reenvia o contexto inteiro a cada passo, e a entrada
  fica 50 a 95 vezes maior que a saída. O que pesa é a parte fixa (system prompt
  com o PROJECT CONTEXT, instruções das tools e a mensagem do lote, com ~100 mil
  caracteres em Users), não o histórico.

---

## Relato 7. Organização dos arquivos

Data: 15/11/2026. Não muda o comportamento: os 109 testes passam e os comandos
do `main.py` são os mesmos (só os caminhos dos exemplos mudaram).

### Por quê

Eram 12 módulos soltos na raiz, com nomes ora em inglês, ora em português. Havia
10 arquivos de agente com 7 linhas cada, uma demo antiga fora do pipeline e um
exemplo duplicado. Agora o código fica todo no pacote `mas/`, dividido por papel
(árvore completa no README):

- `mas/agentes/`: como cada agente é criado, o que recebe e onde roda;
- `mas/pipeline/`: a orquestração (projeto → módulo → etapas);
- `mas/codigo/`: o que é feito em código, sem LLM, sobre o código gerado.

### Onde foi parar cada arquivo

Os relatos anteriores citam os caminhos antigos.

| Antes | Agora |
|---|---|
| `config.py`, `schemas.py` | `mas/config.py`, `mas/schemas.py` |
| `validation.py` | `mas/validacao.py` |
| `render.py` | `mas/relatorio.py` |
| `agents/*.py` (10 arquivos) | `mas/agentes/fabrica.py` (tabela `AGENTES_DE_MODULO`) |
| `system_prompts/system_prompts.py` | `mas/agentes/prompts.py` |
| `system_prompts/templates.py` | `mas/agentes/mensagens.py` |
| `tools/docker_sandbox.py` | `mas/agentes/sandbox.py` |
| `projeto.py` | `mas/pipeline/projeto.py` |
| `pipeline.py` | `mas/pipeline/modulo.py` |
| `implementacao.py` | `mas/pipeline/implementacao.py` |
| `execucao.py` | `mas/pipeline/execucao.py` |
| `layout.py`, `ddl.py`, `assinaturas.py` | `mas/codigo/` |
| gravação em disco e `reparar_caminhos` (em `implementacao.py`) | `mas/codigo/arquivos.py` |
| `ColetaSandbox` (em `implementacao.py`) | `mas/codigo/coleta.py` |
| `criar_agentes`, `ambiente_sandbox` (em `pipeline.py`) | `mas/agentes/fabrica.py` |
| `exemplos/contexto_erp.json` | `exemplos/erp/contexto.json` |
| `tests/test_pipeline.py` | `tests/test_especificacao.py` |
| fábricas de teste em `test_pipeline.py` e `test_projeto.py` | `tests/fakes.py` |

Renomeações dentro do código: `_destino` → `caminho_seguro`; no prompt,
`sandbox()` → `regras_sandbox()` (para não confundir com o módulo
`sandbox.py`); `_executar` (apelido de compatibilidade) → `executar_agente`.

### Removidos

Os arquivos removidos continuam no histórico do git (commit `121a11a`).

- `scripts/arquiteto_chefe.py`: fazia o mesmo que
  `python main.py --projeto <arquivo> --so-contexto`;
- `scripts/demo_file_tools.py` e `tools/manipulacao_de_arquivos.py`: demo antiga,
  fora do pipeline. Os Implementadores usam as tools de arquivo do deepagents
  no sandbox. Saíram junto a variável `FILE_TOOLS_BASE_DIR` e a pasta
  `workspace/` do `.gitignore`;
- `exemplos/user.md`: cópia idêntica de `exemplos/erp/modulos/users.md`.

---

## Relato 6. Implementadores no sandbox Docker

Data: 14/11/2026. Ainda não executada com o Gemini.

### O que muda

Os Implementadores (backend e frontend) agora são *deep agents* (`deepagents`)
que trabalham dentro de um container (`tools/docker_sandbox.py`, imagem
`dockersandbox` com JDK 21, Maven, Node e curl). A pasta do código é montada em
`/workspace`. O agente grava cada arquivo do lote, compila
(`mvn compile`, `npx tsc --noEmit`), roda os testes do lote e, quando a aplicação
já sobe, testa os endpoints com `curl`. Com `SANDBOX=0` tudo volta a ser como
antes.

### Rede: um ambiente por execução

`AmbienteDocker` cria uma rede Docker (`mas-<id>`) com:

| Container | Hostname na rede | Origem |
|---|---|---|
| Banco do `tech_stack` (ex.: `postgres:16-alpine`) | `db` | `database` do PROJECT CONTEXT |
| Cache (ex.: `redis:7-alpine`) | `redis` | `cache` do PROJECT CONTEXT |
| Sandbox | `app` | `SANDBOX_IMAGEM` |

O sandbox já recebe as variáveis de conexão (`SPRING_DATASOURCE_URL`,
`SPRING_DATA_REDIS_HOST`, `DB_HOST`...), e o agente recebe a descrição do
ambiente em cada lote. Um serviço que não sobe não impede a execução: só deixa
de ser anunciado ao agente.

Os dois Implementadores usam **o mesmo** sandbox, e não um container cada um.
Eles rodam em sequência, e a imagem tem as duas toolchains. Assim o
Implementador do frontend pode subir o backend e testar as telas contra a API em
`localhost`, que é o alvo usual do proxy do Vite gerado pela base. No modo
projeto há um ambiente só para todos os módulos: o código, o banco (com as
migrations já aplicadas) e o cache do Maven e do npm são compartilhados entre
eles. Tudo é destruído no fim, mesmo quando há falha.

### O que continua igual

O motivo de usar saída estruturada (relato 2) era **validar antes de aceitar**,
e isso foi mantido. Depois de cada tentativa, `ColetaSandbox`
(`implementacao.py`):
- colhe do disco os arquivos do lote e aplica `reparar_caminhos`;
- desfaz as mudanças fora do lote (restaura arquivos alterados e apaga os
  criados, exceto subprodutos de build como `package-lock.json`). Isso vale
  também para o outro lado do projeto;
- passa o resultado pela mesma `validar_arquivos` e pela mesma nova tentativa;
- quando o lote é reprovado de vez, o disco volta ao estado de antes do lote.

Testes: `tests/test_sandbox.py` (agente falso que grava no disco, mais um teste
de integração com Docker real, pulado quando não há Docker).

### Limites

- `LIMITE_PASSOS_SANDBOX` (padrão 250) é o número de passos do grafo por
  tentativa nos Implementadores. Os outros agentes seguem com
  `LIMITE_PASSOS_AGENTE`. Ao estourar o limite, o que já foi gravado é colhido
  e validado do mesmo jeito.
- `execute` tem timeout também do lado Python. Um servidor iniciado em segundo
  plano sem redirecionar a saída não trava mais o pipeline.

---

## Relato 5. Regeneração do módulo e aplicação no ar

Data: 08/10/2026.

### Regeneração com as correções do relato 4

O código do módulo Users foi regenerado com as correções do relato 4:
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

| | Relato 4 | Relato 5 |
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

## Relato 4. Execução completa, compilação do código gerado e correções

Data: 08/10/2026.

### Correções antes da execução

- **Reparo de caminho trocado** (`implementacao.reparar_caminhos`): resolve a
  causa da falha do relato 3. Quando o modelo escreve a classe `P.X` com o
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

A retomada continuou a execução do relato 3, com `--de planejador`. Os
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

## Relato 3. Limites contra repetição sem fim e primeira execução real

Data: 08/10/2026. Projeto versionado com git a partir deste relato.

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

## Relato 2. Planejador e Implementadores (backend e frontend)

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
executados com o Gemini de verdade** neste relato.

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
  feito neste relato.

---

## Relato 1. Integração do Arquiteto-chefe e execução por projeto

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
