"""Gera a migration SQL de um módulo a partir do modelo de dados validado.

Não usa LLM: o modelo de dados já é estruturado, então a tradução é
determinística. As FKs vão em ALTER TABLE no fim, assim a ordem das tabelas
não importa.
"""
from typing import List, Optional

from schemas import ModuleDataModel, ProjectContext

_BANCOS_SQL = ("postgres", "mysql", "mariadb", "sql", "oracle", "h2", "sqlite")


def usa_sql(contexto: ProjectContext) -> bool:
    nome = contexto.tech_stack.database.name.lower()
    return any(b in nome for b in _BANCOS_SQL)


def _valor(v: Optional[str]) -> Optional[str]:
    if v is None or v.strip().lower() in ("", "null", "none"):
        return None
    return v.strip()


def gerar_ddl(modelo: ModuleDataModel) -> str:
    linhas: List[str] = [
        f"-- Módulo: {modelo.module_name}",
        "-- Gerado automaticamente a partir de 3_modelo_dados.json. Não edite à mão.",
        "",
    ]
    fks: List[str] = []
    indices: List[str] = []

    for e in modelo.entities:
        defs = []
        for c in e.columns:
            partes = [c.name, c.sql_type.strip()]
            extras = [x.strip() for x in c.constraints if x.strip()]
            altas = " ".join(extras).upper()
            if c.primary_key and "PRIMARY KEY" not in altas:
                partes.append("PRIMARY KEY")
            if c.required and not c.primary_key and "NOT NULL" not in altas:
                partes.append("NOT NULL")
            partes += extras
            defs.append("    " + " ".join(partes))
            ref = _valor(c.reference)
            if ref and "." in ref:
                tab, col = ref.split(".", 1)
                fks.append(
                    f"ALTER TABLE {e.table} ADD CONSTRAINT fk_{e.table}_{c.name} "
                    f"FOREIGN KEY ({c.name}) REFERENCES {tab} ({col});")
        defs += ["    " + r.strip() for r in e.constraints if r.strip()]
        linhas.append(f"-- {e.purpose}")
        linhas.append(f"CREATE TABLE {e.table} (\n" + ",\n".join(defs) + "\n);")
        linhas.append("")

        for ix in e.indexes:
            where = _valor(ix.where)
            indices.append(
                f"CREATE {'UNIQUE ' if ix.unique else ''}INDEX {ix.name} "
                f"ON {e.table} ({', '.join(ix.columns)})"
                f"{' WHERE ' + where if where else ''};  -- {ix.requirement}")

    if fks:
        linhas += ["-- Chaves estrangeiras (apenas dentro do módulo)"] + fks + [""]
    if indices:
        linhas += ["-- Índices"] + indices + [""]
    return "\n".join(linhas)
