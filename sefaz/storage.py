"""Persistência local em SQLite para cursores de NSU, notas recebidas e logs (`DistDFe`)."""

from __future__ import annotations

import gzip
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from models.normalizadores import (
    DECIMAL_ZERO,
    limpar_chave_acesso,
    limpar_documento,
    para_data_hora,
    para_decimal,
)
from sefaz.controle_nsu import EstadoCursorNSU, executar_loop_nsu
from sefaz.descompactador import (
    CategoriaSchemaDocZip,
    DocumentoDistDFeProcessado,
    processar_lote_doc_zip,
)
from sefaz.dist_dfe import RespostaDistDFe, formatar_nsu

CAMINHO_DB_PADRAO = "data/nfe_recebidas.db"
StatusPersistenciaDoc = Literal["NOVA", "ATUALIZADA", "EVENTO", "IGNORADA"]
TIPOS_EVENTO_CANCELAMENTO = {"110111", "110112"}

_DDL_TABELAS = """
CREATE TABLE IF NOT EXISTS dfe_cursor (
    ambiente INTEGER PRIMARY KEY,
    ultimo_nsu TEXT NOT NULL,
    max_nsu TEXT NOT NULL,
    ultima_consulta TEXT,
    bloqueado_ate TEXT,
    ultimo_cstat TEXT,
    ultimo_motivo TEXT
);

CREATE TABLE IF NOT EXISTS notas_recebidas (
    chave TEXT PRIMARY KEY,
    nsu TEXT NOT NULL,
    ambiente INTEGER NOT NULL,
    schema_xml TEXT NOT NULL,
    categoria_schema TEXT NOT NULL,
    tipo_documento TEXT NOT NULL,
    modelo TEXT NOT NULL,
    numero TEXT,
    serie TEXT,
    emitente_documento TEXT,
    emitente_nome TEXT,
    emitente_uf TEXT,
    destinatario_documento TEXT,
    destinatario_nome TEXT,
    data_emissao TEXT,
    data_emissao_iso_date TEXT,
    valor_total TEXT NOT NULL,
    valor_total_num REAL NOT NULL,
    situacao TEXT NOT NULL,
    manifestacao_necessaria INTEGER NOT NULL,
    valido INTEGER NOT NULL,
    xml_gzip BLOB,
    atualizado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notas_recebidas_data
    ON notas_recebidas(data_emissao_iso_date DESC, nsu DESC);
CREATE INDEX IF NOT EXISTS idx_notas_recebidas_emitente
    ON notas_recebidas(emitente_documento);

CREATE TABLE IF NOT EXISTS eventos_recebidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chave TEXT NOT NULL,
    nsu TEXT NOT NULL,
    ambiente INTEGER NOT NULL,
    tipo_evento TEXT,
    descricao_evento TEXT,
    data_evento TEXT,
    protocolo TEXT,
    xml_gzip BLOB,
    UNIQUE(chave, nsu, tipo_evento)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ambiente INTEGER NOT NULL,
    cstat TEXT,
    motivo TEXT,
    paginas INTEGER NOT NULL,
    novos_docs INTEGER NOT NULL,
    ultimo_nsu TEXT
);
"""


class RegistroNotaRecebida(BaseModel):
    """Representação persistida de uma nota fiscal recebida via `NFeDistribuicaoDFe`."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    chave: str
    nsu: str
    ambiente: Literal[1, 2] = 2
    schema_xml: str
    categoria_schema: CategoriaSchemaDocZip | str
    tipo_documento: str
    modelo: str
    numero: str = ""
    serie: str = ""
    emitente_documento: str = ""
    emitente_nome: str = ""
    emitente_uf: str | None = None
    destinatario_documento: str = ""
    destinatario_nome: str = ""
    data_emissao: datetime | None = None
    valor_total: Decimal = DECIMAL_ZERO
    situacao: str
    manifestacao_necessaria: bool = False
    valido: bool = True
    atualizado_em: datetime


class RegistroSyncLog(BaseModel):
    """Linha de auditoria de uma execução de sincronização com a SEFAZ."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int
    ts: datetime
    ambiente: Literal[1, 2] = 2
    cstat: str = ""
    motivo: str = ""
    paginas: int = 0
    novos_docs: int = 0
    ultimo_nsu: str = "000000000000000"


class ResumoExecucaoSync(BaseModel):
    """Resumo consolidado de uma execução de `sincronizar_dist_dfe`."""

    model_config = ConfigDict(extra="ignore")

    cursor: EstadoCursorNSU
    paginas_consultadas: int = 0
    documentos_processados: int = 0
    novas_notas: int = 0
    notas_atualizadas: int = 0
    eventos_registrados: int = 0
    bloqueado_656: bool = False
    fila_zerada: bool = False


def resolver_caminho_db(caminho_db: str | Path | None = None) -> Path:
    """Resolve o caminho do banco SQLite a partir do argumento ou de `NFE_DB_PATH`."""
    if caminho_db is not None:
        return Path(caminho_db)
    env_db = os.environ.get("NFE_DB_PATH", "").strip()
    return Path(env_db if env_db else CAMINHO_DB_PADRAO)


def _dt_para_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC).isoformat()
    return dt.isoformat()


def _iso_para_dt(valor: str | None) -> datetime | None:
    if not valor:
        return None
    return para_data_hora(valor)


def _data_filtro_iso(valor: date | datetime | str | None) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    limpo = str(valor).strip()
    if not limpo:
        return None
    return limpo[:10]


def _converter_linha_nota(row: sqlite3.Row) -> RegistroNotaRecebida:
    amb: Literal[1, 2] = 1 if int(row["ambiente"]) == 1 else 2
    dt_atualizado = _iso_para_dt(row["atualizado_em"]) or datetime.now(UTC)
    return RegistroNotaRecebida(
        chave=row["chave"],
        nsu=row["nsu"],
        ambiente=amb,
        schema_xml=row["schema_xml"],
        categoria_schema=row["categoria_schema"],
        tipo_documento=row["tipo_documento"],
        modelo=row["modelo"],
        numero=row["numero"] or "",
        serie=row["serie"] or "",
        emitente_documento=row["emitente_documento"] or "",
        emitente_nome=row["emitente_nome"] or "",
        emitente_uf=row["emitente_uf"],
        destinatario_documento=row["destinatario_documento"] or "",
        destinatario_nome=row["destinatario_nome"] or "",
        data_emissao=_iso_para_dt(row["data_emissao"]),
        valor_total=para_decimal(row["valor_total"]),
        situacao=row["situacao"],
        manifestacao_necessaria=bool(row["manifestacao_necessaria"]),
        valido=bool(row["valido"]),
        atualizado_em=dt_atualizado,
    )


class RepositorioDistDFeSQLite:
    """Repositório SQLite local para cursores de NSU, notas fiscais e eventos recebidos."""

    def __init__(self, caminho_db: str | Path | None = None) -> None:
        self._caminho_db = resolver_caminho_db(caminho_db)
        self._caminho_db.parent.mkdir(parents=True, exist_ok=True)
        self._inicializar_schema()

    @property
    def caminho_db(self) -> Path:
        """Retorna o caminho físico do arquivo SQLite."""
        return self._caminho_db

    def _conectar(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._caminho_db)
        conn.row_factory = sqlite3.Row
        return conn

    def _inicializar_schema(self) -> None:
        with self._conectar() as conn:
            conn.executescript(_DDL_TABELAS)

    def obter_cursor(self, ambiente: Literal[1, 2] = 2) -> EstadoCursorNSU:
        """Lê o `EstadoCursorNSU` do ambiente ou retorna um cursor zerado (`000000000000000`)."""
        amb: Literal[1, 2] = 1 if ambiente == 1 else 2
        with self._conectar() as conn:
            row = conn.execute(
                "SELECT * FROM dfe_cursor WHERE ambiente = ?",
                (amb,),
            ).fetchone()

        if row is None:
            return EstadoCursorNSU(ambiente=amb)

        return EstadoCursorNSU(
            ambiente=amb,
            ultimo_nsu=formatar_nsu(row["ultimo_nsu"]),
            max_nsu=formatar_nsu(row["max_nsu"]),
            ultima_consulta=_iso_para_dt(row["ultima_consulta"]),
            bloqueado_ate=_iso_para_dt(row["bloqueado_ate"]),
            ultimo_cstat=row["ultimo_cstat"],
            ultimo_motivo=row["ultimo_motivo"],
        )

    def salvar_cursor(self, cursor: EstadoCursorNSU) -> None:
        """Faz upsert de `EstadoCursorNSU` na tabela `dfe_cursor`."""
        amb = 1 if cursor.ambiente == 1 else 2
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO dfe_cursor (
                    ambiente, ultimo_nsu, max_nsu, ultima_consulta,
                    bloqueado_ate, ultimo_cstat, ultimo_motivo
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ambiente) DO UPDATE SET
                    ultimo_nsu = excluded.ultimo_nsu,
                    max_nsu = excluded.max_nsu,
                    ultima_consulta = excluded.ultima_consulta,
                    bloqueado_ate = excluded.bloqueado_ate,
                    ultimo_cstat = excluded.ultimo_cstat,
                    ultimo_motivo = excluded.ultimo_motivo
                """,
                (
                    amb,
                    formatar_nsu(cursor.ultimo_nsu),
                    formatar_nsu(cursor.max_nsu),
                    _dt_para_iso(cursor.ultima_consulta),
                    _dt_para_iso(cursor.bloqueado_ate),
                    cursor.ultimo_cstat,
                    cursor.ultimo_motivo,
                ),
            )

    def salvar_documento_processado(
        self,
        item: DocumentoDistDFeProcessado,
        ambiente: Literal[1, 2] = 2,
        agora: datetime | None = None,
    ) -> StatusPersistenciaDoc:
        """Persiste nota (`resNFe`/`procNFe`) ou evento (`procEventoNFe`) com regras de upgrade."""
        doc = item.resultado.documento
        if item.resultado.erro_fatal or doc is None:
            return "IGNORADA"

        chave = limpar_chave_acesso(doc.chave_acesso)
        if not chave:
            return "IGNORADA"

        amb = 1 if ambiente == 1 else 2
        xml_gz = (
            gzip.compress(item.xml_descompactado, compresslevel=6)
            if item.xml_descompactado
            else None
        )
        ts_atual = _dt_para_iso(agora or datetime.now(UTC)) or datetime.now(UTC).isoformat()

        if item.categoria_schema == "EVENTO_NFE" or doc.tipo == "EVENTO":
            with self._conectar() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO eventos_recebidos (
                        chave, nsu, ambiente, tipo_evento, descricao_evento,
                        data_evento, protocolo, xml_gzip
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chave,
                        item.nsu,
                        amb,
                        doc.tipo_evento or "",
                        doc.descricao_evento or "",
                        _dt_para_iso(doc.data_emissao),
                        doc.protocolo or "",
                        xml_gz,
                    ),
                )
                if (doc.tipo_evento or "") in TIPOS_EVENTO_CANCELAMENTO:
                    conn.execute(
                        """
                        UPDATE notas_recebidas
                        SET situacao = 'CANCELADA', atualizado_em = ?
                        WHERE chave = ?
                        """,
                        (ts_atual, chave),
                    )
            return "EVENTO"

        dt_emi_iso = _dt_para_iso(doc.data_emissao)
        dt_emi_date = doc.data_emissao.date().isoformat() if doc.data_emissao is not None else None
        valor_dec = doc.totais.valor_total_nota
        valor_str = f"{valor_dec:.2f}"
        valor_float = float(valor_dec)
        dest_doc = doc.destinatario.documento if doc.destinatario is not None else ""
        dest_nome = doc.destinatario.razao_social if doc.destinatario is not None else ""

        with self._conectar() as conn:
            existente = conn.execute(
                "SELECT categoria_schema, situacao FROM notas_recebidas WHERE chave = ?",
                (chave,),
            ).fetchone()

            # Verifica também se já existe algum evento de cancelamento prévio para esta chave
            ev_cancel = conn.execute(
                """
                SELECT 1 FROM eventos_recebidos
                WHERE chave = ? AND tipo_evento IN ('110111', '110112')
                LIMIT 1
                """,
                (chave,),
            ).fetchone()

            situacao_final = "CANCELADA" if ev_cancel is not None else doc.situacao

            if existente is None:
                conn.execute(
                    """
                    INSERT INTO notas_recebidas (
                        chave, nsu, ambiente, schema_xml, categoria_schema,
                        tipo_documento, modelo, numero, serie,
                        emitente_documento, emitente_nome, emitente_uf,
                        destinatario_documento, destinatario_nome,
                        data_emissao, data_emissao_iso_date,
                        valor_total, valor_total_num, situacao,
                        manifestacao_necessaria, valido, xml_gzip, atualizado_em
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chave,
                        item.nsu,
                        amb,
                        item.schema_xml,
                        item.categoria_schema,
                        doc.tipo,
                        doc.modelo,
                        doc.numero,
                        doc.serie,
                        doc.emitente.documento,
                        doc.emitente.razao_social,
                        doc.emitente.uf,
                        dest_doc,
                        dest_nome,
                        dt_emi_iso,
                        dt_emi_date,
                        valor_str,
                        valor_float,
                        situacao_final,
                        1 if item.manifestacao_necessaria else 0,
                        1 if item.resultado.valido else 0,
                        xml_gz,
                        ts_atual,
                    ),
                )
                return "NOVA"

            # Proteção contra downgrade: nunca sobrescrever NFE_COMPLETA com RESUMO_NFE
            if (
                existente["categoria_schema"] == "NFE_COMPLETA"
                and item.categoria_schema == "RESUMO_NFE"
            ):
                return "IGNORADA"

            # Preserva situação CANCELADA caso a nota já estivesse cancelada no banco
            if existente["situacao"] == "CANCELADA":
                situacao_final = "CANCELADA"

            conn.execute(
                """
                UPDATE notas_recebidas SET
                    nsu = ?,
                    ambiente = ?,
                    schema_xml = ?,
                    categoria_schema = ?,
                    tipo_documento = ?,
                    modelo = ?,
                    numero = ?,
                    serie = ?,
                    emitente_documento = ?,
                    emitente_nome = ?,
                    emitente_uf = ?,
                    destinatario_documento = ?,
                    destinatario_nome = ?,
                    data_emissao = ?,
                    data_emissao_iso_date = ?,
                    valor_total = ?,
                    valor_total_num = ?,
                    situacao = ?,
                    manifestacao_necessaria = ?,
                    valido = ?,
                    xml_gzip = COALESCE(?, xml_gzip),
                    atualizado_em = ?
                WHERE chave = ?
                """,
                (
                    item.nsu,
                    amb,
                    item.schema_xml,
                    item.categoria_schema,
                    doc.tipo,
                    doc.modelo,
                    doc.numero,
                    doc.serie,
                    doc.emitente.documento,
                    doc.emitente.razao_social,
                    doc.emitente.uf,
                    dest_doc,
                    dest_nome,
                    dt_emi_iso,
                    dt_emi_date,
                    valor_str,
                    valor_float,
                    situacao_final,
                    1 if item.manifestacao_necessaria else 0,
                    1 if item.resultado.valido else 0,
                    xml_gz,
                    ts_atual,
                    chave,
                ),
            )
            return "ATUALIZADA"

    def obter_nota_recebida(self, chave: str) -> RegistroNotaRecebida | None:
        """Busca os metadados de uma nota recebida pela chave de acesso de 44 posições."""
        chave_limpa = limpar_chave_acesso(chave)
        with self._conectar() as conn:
            row = conn.execute(
                """
                SELECT
                    chave, nsu, ambiente, schema_xml, categoria_schema,
                    tipo_documento, modelo, numero, serie,
                    emitente_documento, emitente_nome, emitente_uf,
                    destinatario_documento, destinatario_nome,
                    data_emissao, valor_total, situacao,
                    manifestacao_necessaria, valido, atualizado_em
                FROM notas_recebidas
                WHERE chave = ?
                """,
                (chave_limpa,),
            ).fetchone()

        if row is None:
            return None
        return _converter_linha_nota(row)

    def obter_xml_nota_recebida(self, chave: str) -> bytes | None:
        """Recupera e descompacta o XML original (`xml_gzip`) de uma nota recebida."""
        chave_limpa = limpar_chave_acesso(chave)
        with self._conectar() as conn:
            row = conn.execute(
                "SELECT xml_gzip FROM notas_recebidas WHERE chave = ?",
                (chave_limpa,),
            ).fetchone()

        if row is None or not row["xml_gzip"]:
            return None
        return gzip.decompress(bytes(row["xml_gzip"]))

    def listar_notas_recebidas(
        self,
        *,
        ambiente: int | None = None,
        emitente: str | None = None,
        data_inicio: date | str | None = None,
        data_fim: date | str | None = None,
        valor_min: Decimal | float | None = None,
        valor_max: Decimal | float | None = None,
        categoria: str | None = None,
        situacao: str | None = None,
    ) -> list[RegistroNotaRecebida]:
        """Lista notas recebidas ordenadas por `data_emissao DESC, nsu DESC` com filtros."""
        amb_param = int(ambiente) if ambiente in (1, 2) else None

        emit_nome_like: str | None = None
        emit_doc_like: str = ""
        if emitente and emitente.strip():
            termo_bruto = emitente.strip()
            emit_nome_like = f"%{termo_bruto.upper()}%"
            termo_doc = limpar_documento(termo_bruto)
            if termo_doc and all(ch.isalnum() or ch in ".-/ " for ch in termo_bruto):
                emit_doc_like = f"%{termo_doc}%"

        dt_ini = _data_filtro_iso(data_inicio)
        dt_fim = _data_filtro_iso(data_fim)
        v_min = float(valor_min) if valor_min is not None else None
        v_max = float(valor_max) if valor_max is not None else None
        cat_param = categoria.strip().upper() if categoria and categoria.strip() else None
        sit_param = situacao.strip().upper() if situacao and situacao.strip() else None

        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT
                    chave, nsu, ambiente, schema_xml, categoria_schema,
                    tipo_documento, modelo, numero, serie,
                    emitente_documento, emitente_nome, emitente_uf,
                    destinatario_documento, destinatario_nome,
                    data_emissao, valor_total, situacao,
                    manifestacao_necessaria, valido, atualizado_em
                FROM notas_recebidas
                WHERE (? IS NULL OR ambiente = ?)
                  AND (
                      ? IS NULL
                      OR UPPER(emitente_nome) LIKE ?
                      OR (? != '' AND emitente_documento LIKE ?)
                  )
                  AND (? IS NULL OR data_emissao_iso_date >= ?)
                  AND (? IS NULL OR data_emissao_iso_date <= ?)
                  AND (? IS NULL OR valor_total_num >= ?)
                  AND (? IS NULL OR valor_total_num <= ?)
                  AND (? IS NULL OR categoria_schema = ?)
                  AND (? IS NULL OR situacao = ?)
                ORDER BY COALESCE(data_emissao, '') DESC, nsu DESC
                """,
                (
                    amb_param,
                    amb_param,
                    emit_nome_like,
                    emit_nome_like,
                    emit_doc_like,
                    emit_doc_like,
                    dt_ini,
                    dt_ini,
                    dt_fim,
                    dt_fim,
                    v_min,
                    v_min,
                    v_max,
                    v_max,
                    cat_param,
                    cat_param,
                    sit_param,
                    sit_param,
                ),
            ).fetchall()

        return [_converter_linha_nota(r) for r in rows]

    def registrar_log_sync(
        self,
        *,
        ambiente: Literal[1, 2],
        cstat: str,
        motivo: str,
        paginas: int,
        novos_docs: int,
        ultimo_nsu: str,
        ts: datetime | None = None,
    ) -> int:
        """Grava um registro de auditoria na tabela `sync_log` e retorna o `id` gerado."""
        amb = 1 if ambiente == 1 else 2
        ts_str = _dt_para_iso(ts or datetime.now(UTC)) or datetime.now(UTC).isoformat()
        with self._conectar() as conn:
            cur = conn.execute(
                """
                INSERT INTO sync_log (
                    ts, ambiente, cstat, motivo, paginas, novos_docs, ultimo_nsu
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_str,
                    amb,
                    cstat,
                    motivo,
                    paginas,
                    novos_docs,
                    formatar_nsu(ultimo_nsu),
                ),
            )
            return int(cur.lastrowid or 0)

    def listar_logs_sync(self, limite: int = 20) -> list[RegistroSyncLog]:
        """Lista os registros mais recentes de `sync_log`."""
        lim = max(1, int(limite))
        with self._conectar() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_log ORDER BY id DESC LIMIT ?",
                (lim,),
            ).fetchall()

        logs: list[RegistroSyncLog] = []
        for r in rows:
            amb: Literal[1, 2] = 1 if int(r["ambiente"]) == 1 else 2
            logs.append(
                RegistroSyncLog(
                    id=int(r["id"]),
                    ts=_iso_para_dt(r["ts"]) or datetime.now(UTC),
                    ambiente=amb,
                    cstat=r["cstat"] or "",
                    motivo=r["motivo"] or "",
                    paginas=int(r["paginas"]),
                    novos_docs=int(r["novos_docs"]),
                    ultimo_nsu=r["ultimo_nsu"] or "000000000000000",
                )
            )
        return logs


def sincronizar_dist_dfe(
    consultar_fn: Callable[[str], RespostaDistDFe],
    repo: RepositorioDistDFeSQLite,
    ambiente: Literal[1, 2] = 2,
    max_paginas: int = 20,
    relogio_fn: Callable[[], datetime] | None = None,
) -> ResumoExecucaoSync:
    """Executa uma rodada completa de sincronização `DistDFe` persistindo NSU, notas e logs."""
    obter_agora = relogio_fn if relogio_fn is not None else (lambda: datetime.now(UTC))
    cursor_inicial = repo.obter_cursor(ambiente=ambiente)

    resultado_loop = executar_loop_nsu(
        consultar_fn=consultar_fn,
        cursor_inicial=cursor_inicial,
        max_paginas=max_paginas,
        relogio_fn=obter_agora,
        salvar_cursor_fn=repo.salvar_cursor,
    )

    processados = processar_lote_doc_zip(resultado_loop.itens_doc_zip)
    novas = 0
    atualizadas = 0
    eventos = 0
    agora_ref = obter_agora()

    for doc_proc in processados:
        status = repo.salvar_documento_processado(
            doc_proc,
            ambiente=ambiente,
            agora=agora_ref,
        )
        if status == "NOVA":
            novas += 1
        elif status == "ATUALIZADA":
            atualizadas += 1
        elif status == "EVENTO":
            eventos += 1

    repo.registrar_log_sync(
        ambiente=ambiente,
        cstat=resultado_loop.cursor.ultimo_cstat or "",
        motivo=resultado_loop.cursor.ultimo_motivo or "",
        paginas=resultado_loop.paginas_consultadas,
        novos_docs=len(processados),
        ultimo_nsu=resultado_loop.cursor.ultimo_nsu,
        ts=agora_ref,
    )

    return ResumoExecucaoSync(
        cursor=resultado_loop.cursor,
        paginas_consultadas=resultado_loop.paginas_consultadas,
        documentos_processados=len(processados),
        novas_notas=novas,
        notas_atualizadas=atualizadas,
        eventos_registrados=eventos,
        bloqueado_656=resultado_loop.bloqueado_656,
        fila_zerada=resultado_loop.fila_zerada,
    )
