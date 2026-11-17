# Sistema multiagentes — da descrição ao código de um ERP

**Projeto:** Arquiteto-chefe (uma vez) → base de código compartilhada (uma vez)
→ pipeline de cada módulo, na ordem de construção.
**Módulo:** Requisitos → Esclarecedor → Modelador de Dados → Arquiteto de Software
→ Planejador → Implementador backend → Implementador frontend.

O Arquiteto-chefe define o PROJECT CONTEXT (stack, convenções, mapa de módulos,
ordem de construção). Todos os agentes de módulo recebem esse contexto no system
prompt. O Modelador fecha o modelo de dados (tabelas, índices, integridade); o
Arquiteto recebe esse modelo já validado e projeta API, serviços públicos, eventos
e dependências sem poder alterá-lo.
O Planejador junta modelo de dados e arquitetura num plano de arquivos; os
Implementadores escrevem esses arquivos em lotes, dentro de um sandbox Docker
onde compilam e testam o que escreveram. A migration SQL é gerada em código a
partir do modelo de dados (`mas/codigo/ddl.py`), sem LLM.
Cada etapa é validada em código (`mas/validacao.py`) contra os contratos de
`mas/schemas.py`, com nova tentativa automática se reprovar.

## Rodando
```bash
pip install -r requirements.txt
cp .env.example .env        # coloque sua chave

# Projeto inteiro
python main.py --projeto exemplos/erp/sistema.md --modulos-dir exemplos/erp/modulos
python main.py --projeto exemplos/erp/sistema.md --so-contexto          # só o Arquiteto-chefe
python main.py --projeto exemplos/erp/sistema.md \
               --contexto saida/sistema/0_contexto.json --apenas Users,Catalog

# Um módulo (usa exemplos/erp/contexto.json ou --contexto)
python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md                         # até o código
python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md --ate arquiteto_software # só especificação
python main.py --nome "Users" --arquivo exemplos/erp/modulos/users.md --de planejador          # reaproveita 1–4

# Testes (sem chamar LLM; o de integração com Docker é pulado sem Docker)
python -m pytest
```
`--ate` define a última etapa (padrão: `implementador_frontend`); `--de` retoma a
partir de uma etapa lendo do disco os artefatos anteriores.
`VALIDAR=0` desliga a validação em código (útil para depurar prompts).
`IMPL_LOTE` (padrão 5) define quantos arquivos cada chamada de Implementador gera.
Todas as variáveis estão em `.env.example` e `mas/config.py`.

**Sandbox:** as etapas de implementação precisam do Docker e da imagem
`dockersandbox` (JDK, Maven, Node, curl; outra imagem via `SANDBOX_IMAGEM`). O banco
e o cache do `tech_stack` sobem na mesma rede Docker. `SANDBOX=0` volta aos
Implementadores sem sandbox (só saída estruturada).

**Custo:** as etapas de implementação fazem uma chamada por lote de arquivos, e no
sandbox cada chamada tem dezenas de passos que reenviam a conversa inteira. Com o
código das dependências inteiro na mensagem, Users gastou 24 milhões de tokens de
entrada e Catalog 41 milhões (ver `ALTERACOES.md`, relato 8). Hoje as dependências
vão só com assinaturas. Acompanhe em `saida/<projeto>/metricas.md`. Para testar
prompts, prefira `--ate planejador` primeiro.

Saída do projeto em `saida/<projeto>/`: `0_contexto.json`, `contexto.md`,
`projeto.json` (status de cada módulo), `base/` (plano, manifestos e `relatorio.md`
da base), `codigo/backend` e `codigo/frontend` (código de todos os módulos), uma pasta
por módulo e `metricas.md` / `metricas_projeto.json`: tokens, tempo, tentativas e lotes
no total, por módulo e por parte (especificação, planejamento, backend, frontend),
atualizados a cada módulo. Para regenerar: `python -m mas.metricas saida/<projeto>`.
O `metricas.json` de cada pasta é gravado a cada lote, e o `relatorio.md` mostra o
gasto e o relato do agente de cada lote.
Saída de cada módulo: `1_analise.json`, `2_especificacao.json`,
`3_modelo_dados.json`, `4_arquitetura.json`, `5_plano.json`, `6_backend.json` e
`7_frontend.json` (arquivos gerados), `relatorio.md` e `metricas.json`.
Rodando um módulo sozinho, a base e o código ficam em `saida/<modulo>/base` e `saida/<modulo>/codigo`.

## Entradas
- **Descrição do sistema** (`exemplos/erp/sistema.md`): texto livre com uma seção
  `MODULES` no formato `1. Nome: responsabilidade`.
- **Descrição detalhada de módulo** (opcional): `<modulos-dir>/<slug>.md`
  (ex.: `exemplos/erp/modulos/users.md`). Sem ela, usa a linha da seção `MODULES`.
- **PROJECT CONTEXT pronto** (opcional, `--contexto`): pula o Arquiteto-chefe.
  `exemplos/erp/contexto.json` é o padrão quando um módulo roda sozinho.

## Estrutura
```
main.py                  linha de comando (único ponto de entrada)
mas/
├── config.py            variáveis de ambiente e limites
├── schemas.py           contratos entre agentes (saída estruturada de cada um)
├── validacao.py         validação em código da saída de cada etapa
├── relatorio.py         artefatos JSON → markdown
├── agentes/
│   ├── fabrica.py       cria cada agente: system prompt + schema (+ sandbox)
│   ├── prompts.py       system prompts (regras de cada agente)
│   ├── mensagens.py     mensagem de cada etapa (os dados do módulo)
│   └── sandbox.py       container Docker dos Implementadores + rede com banco/cache
├── pipeline/
│   ├── projeto.py       Arquiteto-chefe → base → módulos na ordem de construção
│   ├── modulo.py        etapas 1–7 de um módulo, artefatos e retomada (--de)
│   ├── implementacao.py etapas 5–7 e base compartilhada, em lotes de arquivos
│   └── execucao.py      invoca o agente, valida e tenta de novo (com limites)
└── codigo/              o código gerado, tratado sem LLM
    ├── layout.py        onde cada arquivo pode morar
    ├── ddl.py           migration SQL a partir do modelo de dados
    ├── assinaturas.py   API pública de arquivos (contexto dos Implementadores)
    ├── arquivos.py      gravação em disco e reparo de caminho trocado
    └── coleta.py        colhe o lote gravado no sandbox e desfaz o que está fora dele
exemplos/erp/            sistema.md, contexto.json e modulos/users.md
tests/                   testes sem LLM; agentes falsos e fábricas em tests/fakes.py
```

Mudanças recentes: ver `ALTERACOES.md`.

## Próximos passos
Build e testes do projeto inteiro como validação do pipeline (hoje cada
Implementador compila e testa o próprio lote no sandbox), Consolidador
(coerência entre módulos), revisão humana para alertas de confiança baixa,
execução paralela (LangGraph).
