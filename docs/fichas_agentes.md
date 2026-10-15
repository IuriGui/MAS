# Fichas dos agentes — linha de base (F0)

Cada agente descrito pelos cinco elementos da IBM
([colaboração multiagente](https://www.ibm.com/br-pt/think/topics/multi-agent-collaboration)):
**modelo de base (m)**, **objetivo (o)**, **ambiente (e)**, **percepção de entrada (x)**
e **saída ou ação (y)**. Esta é a descrição do sistema *antes* das fases F1–F6;
serve de referência para comparar o que muda.

## Elementos comuns a todos os agentes

- **m:** o mesmo modelo para todos, definido por `MODEL` em `config.py`
  (padrão `google_genai:gemini-3.8-flash`). Por isso todos os agentes estão sujeitos
  às mesmas falhas do modelo de base, risco que a IBM chama de "falhas
  compartilhadas".
- **e:** o ambiente do agente é **só o prompt**:
  - system prompt = regras do papel (`system_prompts/system_prompts.py`) precedidas
    do PROJECT CONTEXT (`com_contexto`), exceto no Arquiteto-chefe;
  - mensagem = dados montados pelo orquestrador (`system_prompts/templates.py`).

  O agente não tem tools (`create_agent` sem `tools`, em `agents/_base.py`), não lê
  nem escreve em `saida/`, não vê os outros agentes e não troca mensagens com eles.
- **y:** um objeto Pydantic (`schemas.py`). O agente não decide aceitar a própria
  saída: o orquestrador (`execucao.executar_agente`) valida em código
  (`validation.py`) e, se ela for reprovada, reenvia a mesma mensagem com os erros
  anexados, até `MAX_TENTATIVAS`.

## Nível do projeto

### Arquiteto-chefe
- **o:** definir o PROJECT CONTEXT: estilo arquitetural, stack, decisões
  transversais, convenções de nomes, comunicação entre módulos, mapa de módulos,
  ordem de construção, ADRs, riscos e premissas.
- **x:** descrição do sistema + nomes dos módulos declarados (`msg_arquiteto_chefe`).
- **y:** `ProjectContext`, validado por `validar_contexto`.
- Roda uma vez por projeto, antes de todos os outros agentes.

## Nível do módulo (um conjunto por módulo, na ordem de `build_order`)

### Requisitos
- **o:** analisar um módulo, apontar problemas (ambiguidade, lacuna, conflito,
  duplicação, inconsistência) e derivar requisitos funcionais e não-funcionais.
- **x:** descrição do módulo + resumo dos outros módulos (`msg_requisitos`).
- **y:** `ModuleAnalysis`, validado por `validar_analise`.

### Esclarecedor
- **o:** resolver todos os problemas apontados e fechar uma especificação final.
- **x:** descrição original + análise do Requisitos + resumo dos outros módulos
  (`msg_esclarecedor`).
- **y:** `FinalSpecification`, validado por `validar_especificacao`.
- Não pode perguntar ao Requisitos nem a um humano. Decisões de confiança baixa
  vão para `review_alerts`, mas o pipeline não para para revisá-las.

### Modelador de dados
- **o:** projetar o modelo de dados do módulo (tabelas, colunas, índices,
  relacionamentos, referências externas, regras de integridade).
- **x:** especificação final + resumo dos outros módulos (`msg_modelador_dados`).
- **y:** `ModuleDataModel`, validado por `validar_modelo_dados`.

### Arquiteto de software
- **o:** projetar API, serviços públicos, eventos, dependências entre módulos e
  decisões de arquitetura, sem alterar o modelo de dados.
- **x:** especificação final + modelo de dados validado + resumo dos outros módulos,
  com os serviços e eventos já projetados pelos módulos anteriores
  (`msg_arquiteto_software`).
- **y:** `ModuleArchitecture`, validado por `validar_arquitetura`.
- Se falta algo no modelo, só pode registrar a lacuna em `architecture_summary`.
  Não consegue pedir a mudança ao Modelador.

### Planejador
- **o:** transformar especificação, modelo e arquitetura num plano de arquivos
  (backend, frontend e telas). Em modo BASE, planejar o esqueleto compartilhado.
- **x (módulo):** pastas permitidas, migration gerada (`ddl.py`), especificação,
  modelo, arquitetura, plano da base e resumo dos outros módulos (`msg_planejador`).
- **x (base):** módulos, pastas reservadas e rotas de módulo que o router importa
  (`msg_planejador_base`).
- **y:** `ImplementationPlan`, validado por `validar_plano` / `validar_plano_base`.

### Implementador backend
- **o:** escrever o código completo dos arquivos de backend de um lote.
- **x:** especificação, arquitetura, migration, plano, convenções e API pública da
  base, código público de outros módulos e conteúdo dos arquivos de que o lote
  depende (`msg_implementador_backend`, `msg_implementador_base`). O orquestrador
  escolhe esse conteúdo e o corta acima de `IMPL_LIMITE_CONTEXTO`.
- **y:** `GeneratedFiles`, validado por `validar_arquivos`. O orquestrador grava os
  arquivos em disco.
- Não compila nem testa o código. Não lê outros arquivos além dos que recebeu.

### Implementador frontend
- **o:** escrever o código completo dos arquivos de frontend de um lote.
- **x:** requisitos, endpoints da arquitetura, telas, plano, API pública da base e
  conteúdo das dependências (`msg_implementador_frontend`, `msg_implementador_base`).
- **y:** `GeneratedFiles`, validado por `validar_arquivos`.

## Resumo pelos critérios da IBM

| Critério | Situação na linha de base |
|---|---|
| Autonomia no **raciocínio** | Sim: cada agente decide o conteúdo da sua saída. |
| Autonomia no **planejamento** | Não: a ordem das etapas é fixa (`pipeline.ETAPAS`). |
| Autonomia na **chamada de ferramentas** | Não: nenhum agente tem tools. |
| Decidir quando terminou | Não: quem aceita a saída é o orquestrador. |
| Comunicação **direta** (mensagens) | Não existe. |
| Comunicação **indireta** (ambiente compartilhado) | Não: os artefatos ficam em `saida/`, mas só o orquestrador lê e escreve. |
| Coordenação **baseada em papéis** | Sim: um papel por agente. |
| Coordenação **baseada em regras** | Sim: `validation.py` e a ordem fixa das etapas. |
| Execução | Só sequencial. |
| Tolerância a falhas | Um módulo que falha não interrompe os outros. Uma etapa que falha encerra o módulo. |

As métricas desta linha de base são geradas por `scripts/linha_de_base.py`
(ver README).

## Depois da F2 (autonomia)

O que mudou nos elementos de cada agente com `AUTONOMIA=1`:

- **e (ambiente):** além do prompt, o agente percebe o ambiente compartilhado pelas
  tools de leitura (`agents/ferramentas.py`). O conjunto de tools depende do papel:

  | Agente | Tools |
  |---|---|
  | Arquiteto-chefe | nenhuma (roda antes de existir ambiente) |
  | Requisitos, Esclarecedor, Modelador, Arquiteto de software | `get_artifact`, `get_contracts` |
  | Planejador | `get_artifact`, `get_contracts`, `list_files`, `get_signatures` |
  | Implementadores | as cinco, incluindo `read_file` |

- **x (percepção):** a mensagem continua trazendo os dados principais da etapa. O
  resto o agente busca quando precisa. Nos Implementadores, as dependências do
  lote vêm só como assinaturas, e o conteúdo inteiro é lido sob demanda.
- **y (ação):** o agente decide quando submeter. A submissão é validada no ato e,
  se reprovada, volta para o agente com os erros. Ele corrige dentro do próprio
  loop, com um orçamento de `LIMITE_CHAMADAS_AGENTE` chamadas.

| Critério | Linha de base | Depois da F2 |
|---|---|---|
| Autonomia na **chamada de ferramentas** | Não | Sim: o agente escolhe o que ler no ambiente |
| Decidir quando terminou | Não | Em parte: o agente submete quando quer, e o ambiente aceita ou reprova |
| Comunicação **indireta** (ambiente compartilhado) | Não | Sim: os agentes leem o que os outros publicaram |
| Autonomia no **planejamento** | Não | Não: a ordem das etapas segue fixa (F3) |
| Comunicação **direta** (mensagens) | Não | Não (F3) |
