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

from models.fiscal import DocumentoFiscal, ResultadoAnalise
from models.normalizadores import (
    DECIMAL_ZERO,
    limpar_chave_acesso,
    limpar_documento,
    para_data_hora,
    para_decimal,
)
from reports.resumo import ResumoLote, gerar_resumo_lote
from sefaz.adn import EstadoCursorADN
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
# Eventos do ADN (NFS-e) que efetivamente cancelam a nota. A *solicitação* de cancelamento
# para análise fiscal NÃO cancela; é só um pedido.
TIPOS_EVENTO_CANCELAMENTO_ADN = {
    "CANCELAMENTO",
    "CANCELAMENTO_POR_SUBSTITUICAO",
    "CANCELAMENTO_DEFERIDO_ANALISE_FISCAL",
    "CANCELAMENTO_POR_OFICIO",
}
_TODOS_CANCELAMENTO = sorted(TIPOS_EVENTO_CANCELAMENTO | TIPOS_EVENTO_CANCELAMENTO_ADN)
ORIGEM_DISTDFE = "DISTDFE"
ORIGEM_ADN = "ADN"

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

CREATE TABLE IF NOT EXISTS adn_cursor (
    ambiente INTEGER PRIMARY KEY,
    ultimo_nsu INTEGER NOT NULL DEFAULT 0,
    ultima_consulta TEXT,
    bloqueado_ate TEXT,
    ultimo_status TEXT,
    ultimo_motivo TEXT
);

CREATE TABLE IF NOT EXISTS lotes_upload (
    lote_id TEXT PRIMARY KEY,
    nome_rotulo TEXT NOT NULL,
    origem TEXT NOT NULL,
    criado_em TEXT NOT NULL,
    total_arquivos INTEGER NOT NULL,
    validos INTEGER NOT NULL,
    com_erros INTEGER NOT NULL,
    com_avisos INTEGER NOT NULL,
    valor_total_notas TEXT NOT NULL,
    resumo_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lotes_upload_itens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id TEXT NOT NULL,
    indice INTEGER NOT NULL,
    arquivo_origem TEXT NOT NULL,
    chave_acesso TEXT,
    tipo_documento TEXT,
    valido INTEGER NOT NULL,
    resultado_json TEXT NOT NULL,
    xml_gzip BLOB,
    UNIQUE(lote_id, indice)
);

CREATE INDEX IF NOT EXISTS idx_lotes_upload_criado_em
    ON lotes_upload(criado_em DESC);
CREATE INDEX IF NOT EXISTS idx_lotes_upload_itens_lote_id
    ON lotes_upload_itens(lote_id, indice ASC);
CREATE INDEX IF NOT EXISTS idx_lotes_itens_chave
    ON lotes_upload_itens(chave_acesso);
"""

# Migração ADITIVA do banco (só acrescenta colunas e índices; nunca apaga nem reescreve linhas).
# `ALTER TABLE ... ADD COLUMN ... DEFAULT` não reescreve as linhas existentes no SQLite.
_COLUNAS_NOVAS: tuple[tuple[str, str, str], ...] = (
    ("notas_recebidas", "origem", "TEXT NOT NULL DEFAULT 'DISTDFE'"),
    ("notas_recebidas", "papel", "TEXT"),
    ("sync_log", "origem", "TEXT NOT NULL DEFAULT 'NFE'"),
    ("sync_log", "tentativas", "INTEGER NOT NULL DEFAULT 1"),
    ("sync_log", "falhas_conexao", "TEXT"),
)
_INDICES_NOVOS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_notas_recebidas_origem ON notas_recebidas(origem)",
    "CREATE INDEX IF NOT EXISTS idx_notas_recebidas_papel ON notas_recebidas(papel)",
    "CREATE INDEX IF NOT EXISTS idx_notas_recebidas_tipo ON notas_recebidas(tipo_documento)",
)


def migrar_schema_aditivo(conn: sqlite3.Connection) -> list[str]:
    """Acrescenta colunas/índices ausentes. Idempotente. Retorna o que foi acrescentado."""
    acrescentados: list[str] = []
    for tabela, coluna, definicao in _COLUNAS_NOVAS:
        existentes = {r[1] for r in conn.execute(f"PRAGMA table_info({tabela})")}
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")
            acrescentados.append(f"{tabela}.{coluna}")
    for ddl in _INDICES_NOVOS:
        conn.execute(ddl)
    return acrescentados


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
    origem: str = ORIGEM_DISTDFE
    papel: str | None = None
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
    origem: str = "NFE"
    tentativas: int = 1
    falhas_conexao: str = ""


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


class ResumoHistoricoLote(BaseModel):
    """Metadados persistidos de um lote importado via upload ou demonstração."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    lote_id: str
    nome_rotulo: str
    origem: Literal["upload", "demo"] = "upload"
    criado_em: datetime
    total_arquivos: int = 0
    validos: int = 0
    com_erros: int = 0
    com_avisos: int = 0
    valor_total_notas: Decimal = DECIMAL_ZERO


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
        origem=row["origem"] or ORIGEM_DISTDFE,
        papel=row["papel"],
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
            migrar_schema_aditivo(conn)

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
                WHERE chave = ? AND tipo_evento IN (?, ?, ?, ?, ?, ?)
                LIMIT 1
                """,
                (chave, *_TODOS_CANCELAMENTO),
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

    def obter_cursor_adn(self, ambiente: Literal[1, 2] = 2) -> EstadoCursorADN:
        """Lê o cursor de NSU do ADN (independente do `dfe_cursor` do NFeDistribuicaoDFe)."""
        amb: Literal[1, 2] = 1 if ambiente == 1 else 2
        with self._conectar() as conn:
            row = conn.execute("SELECT * FROM adn_cursor WHERE ambiente = ?", (amb,)).fetchone()
        if row is None:
            return EstadoCursorADN(ambiente=amb)
        return EstadoCursorADN(
            ambiente=amb,
            ultimo_nsu=int(row["ultimo_nsu"]),
            ultima_consulta=_iso_para_dt(row["ultima_consulta"]),
            bloqueado_ate=_iso_para_dt(row["bloqueado_ate"]),
            ultimo_status=row["ultimo_status"],
            ultimo_motivo=row["ultimo_motivo"],
        )

    def salvar_cursor_adn(self, cursor: EstadoCursorADN) -> None:
        """Upsert do cursor do ADN."""
        amb = 1 if cursor.ambiente == 1 else 2
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT INTO adn_cursor (
                    ambiente, ultimo_nsu, ultima_consulta, bloqueado_ate,
                    ultimo_status, ultimo_motivo
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ambiente) DO UPDATE SET
                    ultimo_nsu = excluded.ultimo_nsu,
                    ultima_consulta = excluded.ultima_consulta,
                    bloqueado_ate = excluded.bloqueado_ate,
                    ultimo_status = excluded.ultimo_status,
                    ultimo_motivo = excluded.ultimo_motivo
                """,
                (
                    amb,
                    int(cursor.ultimo_nsu),
                    _dt_para_iso(cursor.ultima_consulta),
                    _dt_para_iso(cursor.bloqueado_ate),
                    cursor.ultimo_status,
                    cursor.ultimo_motivo,
                ),
            )

    def salvar_nfse_adn(
        self,
        *,
        chave: str,
        nsu: int,
        doc: DocumentoFiscal,
        xml_bytes: bytes,
        papel: str | None,
        ambiente: Literal[1, 2] = 2,
        agora: datetime | None = None,
    ) -> StatusPersistenciaDoc:
        """Persiste uma NFS-e do ADN, deduplicando por chave (50 posições) e sem rebaixar."""
        chave_limpa = limpar_chave_acesso(chave)
        if not chave_limpa:
            return "IGNORADA"
        amb = 1 if ambiente == 1 else 2
        ts_atual = _dt_para_iso(agora or datetime.now(UTC)) or datetime.now(UTC).isoformat()
        xml_gz = gzip.compress(xml_bytes, compresslevel=6) if xml_bytes else None
        dest = doc.destinatario
        dt_emi_iso = _dt_para_iso(doc.data_emissao)
        dt_emi_date = doc.data_emissao.date().isoformat() if doc.data_emissao else None
        valor_dec = doc.totais.valor_total_nota
        nsu_txt = str(nsu).zfill(15)

        with self._conectar() as conn:
            existente = conn.execute(
                "SELECT situacao FROM notas_recebidas WHERE chave = ?", (chave_limpa,)
            ).fetchone()
            cancelada_antes = conn.execute(
                """
                SELECT 1 FROM eventos_recebidos
                WHERE chave = ? AND tipo_evento IN (?, ?, ?, ?, ?, ?) LIMIT 1
                """,
                (chave_limpa, *_TODOS_CANCELAMENTO),
            ).fetchone()
            situacao = "CANCELADA" if cancelada_antes is not None else doc.situacao

            valores = (
                nsu_txt,
                amb,
                "NFSe",
                "NFSE_NACIONAL",
                doc.tipo,
                doc.modelo,
                doc.numero,
                doc.serie,
                doc.emitente.documento,
                doc.emitente.razao_social,
                doc.emitente.uf,
                dest.documento if dest else "",
                dest.razao_social if dest else "",
                dt_emi_iso,
                dt_emi_date,
                f"{valor_dec:.2f}",
                float(valor_dec),
            )
            if existente is None:
                conn.execute(
                    """
                    INSERT INTO notas_recebidas (
                        nsu, ambiente, schema_xml, categoria_schema, tipo_documento, modelo,
                        numero, serie, emitente_documento, emitente_nome, emitente_uf,
                        destinatario_documento, destinatario_nome, data_emissao,
                        data_emissao_iso_date, valor_total, valor_total_num,
                        situacao, manifestacao_necessaria, valido, xml_gzip, atualizado_em,
                        origem, papel, chave
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, 0, 1, ?, ?, 'ADN', ?, ?)
                    """,
                    (*valores, situacao, xml_gz, ts_atual, papel, chave_limpa),
                )
                return "NOVA"

            # Nunca rebaixa: nota já autorizada/cancelada não volta a "sem protocolo".
            if doc.situacao == "SEM_PROTOCOLO" and existente["situacao"] in (
                "AUTORIZADA",
                "CANCELADA",
            ):
                return "IGNORADA"
            if existente["situacao"] == "CANCELADA":
                situacao = "CANCELADA"
            conn.execute(
                """
                UPDATE notas_recebidas SET
                    nsu = ?, ambiente = ?, schema_xml = ?, categoria_schema = ?,
                    tipo_documento = ?, modelo = ?, numero = ?, serie = ?,
                    emitente_documento = ?, emitente_nome = ?, emitente_uf = ?,
                    destinatario_documento = ?, destinatario_nome = ?,
                    data_emissao = ?, data_emissao_iso_date = ?,
                    valor_total = ?, valor_total_num = ?, situacao = ?,
                    xml_gzip = COALESCE(?, xml_gzip), atualizado_em = ?,
                    papel = COALESCE(?, papel)
                WHERE chave = ?
                """,
                (*valores, situacao, xml_gz, ts_atual, papel, chave_limpa),
            )
            return "ATUALIZADA"

    def salvar_evento_adn(
        self,
        *,
        chave: str,
        nsu: int,
        tipo_evento: str,
        xml_bytes: bytes,
        data_evento: datetime | None,
        ambiente: Literal[1, 2] = 2,
        agora: datetime | None = None,
    ) -> StatusPersistenciaDoc:
        """Guarda o evento e, se for cancelamento efetivo, marca a nota (sem alterar mais nada)."""
        chave_limpa = limpar_chave_acesso(chave)
        if not chave_limpa:
            return "IGNORADA"
        amb = 1 if ambiente == 1 else 2
        ts_atual = _dt_para_iso(agora or datetime.now(UTC)) or datetime.now(UTC).isoformat()
        xml_gz = gzip.compress(xml_bytes, compresslevel=6) if xml_bytes else None
        with self._conectar() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO eventos_recebidos (
                    chave, nsu, ambiente, tipo_evento, descricao_evento,
                    data_evento, protocolo, xml_gzip
                ) VALUES (?, ?, ?, ?, ?, ?, '', ?)
                """,
                (
                    chave_limpa,
                    str(nsu).zfill(15),
                    amb,
                    tipo_evento,
                    tipo_evento,
                    _dt_para_iso(data_evento),
                    xml_gz,
                ),
            )
            if tipo_evento in TIPOS_EVENTO_CANCELAMENTO_ADN:
                conn.execute(
                    "UPDATE notas_recebidas SET situacao = 'CANCELADA', atualizado_em = ? "
                    "WHERE chave = ?",
                    (ts_atual, chave_limpa),
                )
        return "EVENTO"

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
                    manifestacao_necessaria, valido, origem, papel, atualizado_em
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
        origem: str | None = None,
        papel: str | None = None,
        tipo: str | None = None,
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
        ori_param = origem.strip().upper() if origem and origem.strip() else None
        pap_param = papel.strip().upper() if papel and papel.strip() else None
        tip_param = tipo.strip().upper() if tipo and tipo.strip() else None

        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT
                    chave, nsu, ambiente, schema_xml, categoria_schema,
                    tipo_documento, modelo, numero, serie,
                    emitente_documento, emitente_nome, emitente_uf,
                    destinatario_documento, destinatario_nome,
                    data_emissao, valor_total, situacao,
                    manifestacao_necessaria, valido, origem, papel, atualizado_em
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
                  AND (? IS NULL OR origem = ?)
                  AND (? IS NULL OR papel = ?)
                  AND (? IS NULL OR tipo_documento = ?)
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
                    ori_param,
                    ori_param,
                    pap_param,
                    pap_param,
                    tip_param,
                    tip_param,
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
        origem: Literal["NFE", "NFSE"] = "NFE",
        tentativas: int = 1,
        falhas_conexao: str = "",
    ) -> int:
        """Grava um registro de auditoria na tabela `sync_log` e retorna o `id` gerado."""
        amb = 1 if ambiente == 1 else 2
        ts_str = _dt_para_iso(ts or datetime.now(UTC)) or datetime.now(UTC).isoformat()
        with self._conectar() as conn:
            cur = conn.execute(
                """
                INSERT INTO sync_log (
                    ts, ambiente, cstat, motivo, paginas, novos_docs, ultimo_nsu, origem,
                    tentativas, falhas_conexao
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts_str,
                    amb,
                    cstat,
                    motivo,
                    paginas,
                    novos_docs,
                    str(ultimo_nsu) if origem == "NFSE" else formatar_nsu(ultimo_nsu),
                    origem,
                    max(1, int(tentativas)),
                    falhas_conexao[:2000],
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
                    origem=r["origem"] or "NFE",
                    tentativas=int(r["tentativas"] or 1),
                    falhas_conexao=r["falhas_conexao"] or "",
                )
            )
        return logs

    def salvar_lote_upload(
        self,
        lote_id: str,
        criado_em: datetime,
        resumo: ResumoLote,
        resultados: list[ResultadoAnalise],
        xmls_brutos: dict[str, bytes],
        nome_rotulo: str = "",
        origem: Literal["upload", "demo"] = "upload",
    ) -> ResumoHistoricoLote:
        """Grava o cabeçalho do lote e cada item analisado (com XML compactado em `gzip`)."""
        origem_norm: Literal["upload", "demo"] = "demo" if origem == "demo" else "upload"
        rotulo_final = nome_rotulo.strip()
        if not rotulo_final:
            if origem_norm == "demo":
                rotulo_final = "Lote de Demonstração"
            else:
                nomes = [r.arquivo_origem for r in resultados[:2] if r.arquivo_origem]
                amostra = ", ".join(nomes)
                if len(resultados) > 2:
                    amostra = f"{amostra}, ..."
                qtd = len(resultados)
                plural = "arquivo" if qtd == 1 else "arquivos"
                rotulo_final = f"{qtd} {plural} ({amostra})" if amostra else f"Lote {lote_id}"

        criado_iso = _dt_para_iso(criado_em) or datetime.now(UTC).isoformat()
        cont = resumo.contagem
        valor_str = f"{resumo.valor_total_notas:.2f}"
        resumo_json_str = resumo.model_dump_json()

        with self._conectar() as conn:
            conn.execute("DELETE FROM lotes_upload_itens WHERE lote_id = ?", (lote_id,))
            conn.execute(
                """
                INSERT INTO lotes_upload (
                    lote_id, nome_rotulo, origem, criado_em,
                    total_arquivos, validos, com_erros, com_avisos,
                    valor_total_notas, resumo_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lote_id) DO UPDATE SET
                    nome_rotulo = excluded.nome_rotulo,
                    origem = excluded.origem,
                    criado_em = excluded.criado_em,
                    total_arquivos = excluded.total_arquivos,
                    validos = excluded.validos,
                    com_erros = excluded.com_erros,
                    com_avisos = excluded.com_avisos,
                    valor_total_notas = excluded.valor_total_notas,
                    resumo_json = excluded.resumo_json
                """,
                (
                    lote_id,
                    rotulo_final,
                    origem_norm,
                    criado_iso,
                    cont.total_arquivos,
                    cont.validos,
                    cont.com_erros,
                    cont.com_avisos,
                    valor_str,
                    resumo_json_str,
                ),
            )

            for idx, res in enumerate(resultados):
                doc = res.documento
                chave_orig = doc.chave_acesso if doc is not None else ""
                chave_limpa = limpar_chave_acesso(chave_orig) if chave_orig else ""
                tipo_doc = doc.tipo if doc is not None else "ERRO_XML"

                xml_bytes = (
                    xmls_brutos.get(str(idx))
                    or (xmls_brutos.get(chave_orig) if chave_orig else None)
                    or (xmls_brutos.get(chave_limpa) if chave_limpa else None)
                )
                xml_gz = gzip.compress(xml_bytes, compresslevel=6) if xml_bytes else None

                conn.execute(
                    """
                    INSERT INTO lotes_upload_itens (
                        lote_id, indice, arquivo_origem, chave_acesso,
                        tipo_documento, valido, resultado_json, xml_gzip
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lote_id,
                        idx,
                        res.arquivo_origem,
                        chave_limpa or chave_orig or None,
                        tipo_doc,
                        1 if res.valido else 0,
                        res.model_dump_json(),
                        xml_gz,
                    ),
                )

        return ResumoHistoricoLote(
            lote_id=lote_id,
            nome_rotulo=rotulo_final,
            origem=origem_norm,
            criado_em=_iso_para_dt(criado_iso) or criado_em,
            total_arquivos=cont.total_arquivos,
            validos=cont.validos,
            com_erros=cont.com_erros,
            com_avisos=cont.com_avisos,
            valor_total_notas=resumo.valor_total_notas,
        )

    def obter_lote_upload(
        self, lote_id: str
    ) -> tuple[datetime, ResumoLote, list[ResultadoAnalise], dict[str, bytes]] | None:
        """Reconstrói um lote completo (`criado_em`, `resumo`, `resultados`, `xmls_brutos`)."""
        with self._conectar() as conn:
            cab = conn.execute(
                "SELECT criado_em, resumo_json FROM lotes_upload WHERE lote_id = ?",
                (lote_id,),
            ).fetchone()
            if cab is None:
                return None

            itens_rows = conn.execute(
                """
                SELECT indice, resultado_json, xml_gzip
                FROM lotes_upload_itens
                WHERE lote_id = ?
                ORDER BY indice ASC
                """,
                (lote_id,),
            ).fetchall()

        criado_em = _iso_para_dt(cab["criado_em"]) or datetime.now(UTC)
        resultados: list[ResultadoAnalise] = []
        xmls_brutos: dict[str, bytes] = {}

        for idx, row in enumerate(itens_rows):
            res = ResultadoAnalise.model_validate_json(row["resultado_json"])
            resultados.append(res)
            if row["xml_gzip"]:
                xml_bytes = gzip.decompress(bytes(row["xml_gzip"]))
                xmls_brutos[str(idx)] = xml_bytes
                doc = res.documento
                if doc is not None and doc.chave_acesso:
                    chave_orig = doc.chave_acesso
                    chave_limpa = limpar_chave_acesso(chave_orig)
                    if chave_orig not in xmls_brutos:
                        xmls_brutos[chave_orig] = xml_bytes
                    if chave_limpa and chave_limpa not in xmls_brutos:
                        xmls_brutos[chave_limpa] = xml_bytes

        try:
            resumo = ResumoLote.model_validate_json(cab["resumo_json"])
        except Exception:
            resumo = gerar_resumo_lote(resultados)

        return criado_em, resumo, resultados, xmls_brutos

    def listar_lotes_upload(self, limite: int = 50) -> list[ResumoHistoricoLote]:
        """Lista o histórico de lotes salvos ordenado por `criado_em DESC`."""
        lim = max(1, int(limite))
        with self._conectar() as conn:
            rows = conn.execute(
                """
                SELECT
                    lote_id, nome_rotulo, origem, criado_em,
                    total_arquivos, validos, com_erros, com_avisos,
                    valor_total_notas
                FROM lotes_upload
                ORDER BY criado_em DESC, rowid DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()

        historico: list[ResumoHistoricoLote] = []
        for r in rows:
            orig: Literal["upload", "demo"] = "demo" if r["origem"] == "demo" else "upload"
            historico.append(
                ResumoHistoricoLote(
                    lote_id=r["lote_id"],
                    nome_rotulo=r["nome_rotulo"],
                    origem=orig,
                    criado_em=_iso_para_dt(r["criado_em"]) or datetime.now(UTC),
                    total_arquivos=int(r["total_arquivos"]),
                    validos=int(r["validos"]),
                    com_erros=int(r["com_erros"]),
                    com_avisos=int(r["com_avisos"]),
                    valor_total_notas=para_decimal(r["valor_total_notas"]),
                )
            )
        return historico

    def excluir_lote_upload(self, lote_id: str) -> bool:
        """Remove um lote e seus itens do SQLite. Retorna `True` se o lote existia."""
        with self._conectar() as conn:
            conn.execute("DELETE FROM lotes_upload_itens WHERE lote_id = ?", (lote_id,))
            cur = conn.execute("DELETE FROM lotes_upload WHERE lote_id = ?", (lote_id,))
            return int(cur.rowcount or 0) > 0

    def limpar_lotes_upload(self, apenas_demo: bool = False) -> int:
        """Remove todos os lotes (ou apenas os de `origem == 'demo'`) e retorna a contagem."""
        with self._conectar() as conn:
            if apenas_demo:
                conn.execute(
                    """
                    DELETE FROM lotes_upload_itens
                    WHERE lote_id IN (SELECT lote_id FROM lotes_upload WHERE origem = 'demo')
                    """
                )
                cur = conn.execute("DELETE FROM lotes_upload WHERE origem = 'demo'")
            else:
                conn.execute("DELETE FROM lotes_upload_itens")
                cur = conn.execute("DELETE FROM lotes_upload")
            return int(cur.rowcount or 0)

    def obter_acervo_unificado(
        self,
        *,
        incluir_xmls_brutos: bool = False,
    ) -> tuple[datetime, ResumoLote, list[ResultadoAnalise], dict[str, bytes]]:
        """Consolida todas as notas de todos os lotes no SQLite, deduplicando chaves repetidas.

        Por padrão (`incluir_xmls_brutos=False`) não lê nem descompacta a coluna `xml_gzip`,
        mantendo o carregamento leve e instantâneo mesmo em acervos com 15.000+ notas.
        """
        tipos_principais = {"NFE", "NFCE", "NFSE", "RESUMO"}
        colunas = (
            "l.criado_em, i.resultado_json, i.xml_gzip"
            if incluir_xmls_brutos
            else "l.criado_em, i.resultado_json"
        )
        conn = self._conectar()
        try:
            rows = conn.execute(
                f"""
                SELECT {colunas}
                FROM lotes_upload_itens i
                INNER JOIN lotes_upload l ON l.lote_id = i.lote_id
                ORDER BY l.criado_em DESC, l.rowid DESC, i.indice ASC
                """  # noqa: S608
            ).fetchall()
        finally:
            conn.close()

        criado_em_ref = (
            (_iso_para_dt(rows[0]["criado_em"]) or datetime.now(UTC)) if rows else datetime.now(UTC)
        )
        resultados_unificados: list[ResultadoAnalise] = []
        xmls_brutos: dict[str, bytes] = {}
        chaves_vistas: set[str] = set()

        for row in rows:
            res = ResultadoAnalise.model_validate_json(row["resultado_json"])
            doc = res.documento
            chave_orig = doc.chave_acesso if doc is not None else ""
            chave_limpa = limpar_chave_acesso(chave_orig) if chave_orig else ""

            if doc is not None and doc.tipo in tipos_principais and chave_limpa:
                if chave_limpa in chaves_vistas:
                    continue
                chaves_vistas.add(chave_limpa)

            idx_unificado = len(resultados_unificados)
            resultados_unificados.append(res)

            if incluir_xmls_brutos and row["xml_gzip"]:
                xml_bytes = gzip.decompress(bytes(row["xml_gzip"]))
                xmls_brutos[str(idx_unificado)] = xml_bytes
                if chave_orig and chave_orig not in xmls_brutos:
                    xmls_brutos[chave_orig] = xml_bytes
                if chave_limpa and chave_limpa not in xmls_brutos:
                    xmls_brutos[chave_limpa] = xml_bytes

        resumo = gerar_resumo_lote(resultados_unificados)
        return criado_em_ref, resumo, resultados_unificados, xmls_brutos

    def obter_nota_e_xml_no_escopo(
        self,
        lote_id: str,
        identificador: str,
    ) -> tuple[ResultadoAnalise, bytes] | None:
        """Busca cirurgicamente 1 nota e descompacta apenas o seu `xml_gzip` no SQLite."""
        escopo = lote_id.strip()
        ident = identificador.strip()
        if not escopo or not ident:
            return None

        eh_acervo = escopo.lower() == "acervo"
        eh_indice = ident.isdigit() and len(ident) < 44
        tipos_principais = {"NFE", "NFCE", "NFSE", "RESUMO"}

        conn = self._conectar()
        try:
            row: sqlite3.Row | None = None
            if eh_indice:
                indice_alvo = int(ident)
                if eh_acervo:
                    # Varre apenas 3 colunas escalares (zero JSON/BLOB) para localizar o PK alvo
                    cand_rows = conn.execute(
                        """
                        SELECT i.id, i.chave_acesso, i.tipo_documento
                        FROM lotes_upload_itens i
                        INNER JOIN lotes_upload l ON l.lote_id = i.lote_id
                        ORDER BY l.criado_em DESC, l.rowid DESC, i.indice ASC
                        """
                    ).fetchall()
                    chaves_vistas: set[str] = set()
                    contador = 0
                    row_id_alvo: int | None = None
                    for cand in cand_rows:
                        tipo_doc = cand["tipo_documento"] or ""
                        chave_limpa = (
                            limpar_chave_acesso(cand["chave_acesso"])
                            if cand["chave_acesso"]
                            else ""
                        )
                        if tipo_doc in tipos_principais and chave_limpa:
                            if chave_limpa in chaves_vistas:
                                continue
                            chaves_vistas.add(chave_limpa)
                        if contador == indice_alvo:
                            row_id_alvo = int(cand["id"])
                            break
                        contador += 1

                    if row_id_alvo is not None:
                        row = conn.execute(
                            """
                            SELECT resultado_json, xml_gzip
                            FROM lotes_upload_itens
                            WHERE id = ?
                            """,
                            (row_id_alvo,),
                        ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT resultado_json, xml_gzip
                        FROM lotes_upload_itens
                        WHERE lote_id = ? AND indice = ?
                        LIMIT 1
                        """,
                        (escopo, indice_alvo),
                    ).fetchone()
            else:
                chave_limpa = limpar_chave_acesso(ident)
                chave_b = chave_limpa or ident
                if eh_acervo:
                    row = conn.execute(
                        """
                        SELECT i.resultado_json, i.xml_gzip
                        FROM lotes_upload_itens i
                        INNER JOIN lotes_upload l ON l.lote_id = i.lote_id
                        WHERE i.chave_acesso IN (?, ?)
                        ORDER BY l.criado_em DESC, l.rowid DESC, i.indice ASC
                        LIMIT 1
                        """,
                        (ident, chave_b),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT resultado_json, xml_gzip
                        FROM lotes_upload_itens
                        WHERE lote_id = ? AND chave_acesso IN (?, ?)
                        ORDER BY indice ASC
                        LIMIT 1
                        """,
                        (escopo, ident, chave_b),
                    ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        resultado = ResultadoAnalise.model_validate_json(row["resultado_json"])
        xml_bytes = gzip.decompress(bytes(row["xml_gzip"])) if row["xml_gzip"] else b""
        return resultado, xml_bytes

    def limpar_notas_recebidas(self, zerar_cursor: bool = True) -> int:
        """Remove notas e eventos recebidos (e opcionalmente `dfe_cursor` e `sync_log`)."""
        conn = self._conectar()
        try:
            with conn:
                cur_notas = conn.execute("DELETE FROM notas_recebidas")
                conn.execute("DELETE FROM eventos_recebidos")
                if zerar_cursor:
                    conn.execute("DELETE FROM dfe_cursor")
                    conn.execute("DELETE FROM adn_cursor")
                    conn.execute("DELETE FROM sync_log")
                return int(cur_notas.rowcount or 0)
        finally:
            conn.close()

    def resetar_banco_completo(self, executar_vacuum: bool = True) -> dict[str, int]:
        """Apaga lotes de upload, notas recebidas, eventos, cursores e logs do SQLite local."""
        conn = self._conectar()
        try:
            with conn:
                conn.execute("DELETE FROM lotes_upload_itens")
                cur_lotes = conn.execute("DELETE FROM lotes_upload")
                cur_recebidas = conn.execute("DELETE FROM notas_recebidas")
                conn.execute("DELETE FROM eventos_recebidos")
                conn.execute("DELETE FROM dfe_cursor")
                conn.execute("DELETE FROM adn_cursor")
                conn.execute("DELETE FROM sync_log")
                lotes_removidos = int(cur_lotes.rowcount or 0)
                recebidas_removidas = int(cur_recebidas.rowcount or 0)

            if executar_vacuum:
                conn.isolation_level = None
                conn.execute("VACUUM")
        finally:
            conn.close()

        return {
            "lotes_removidos": lotes_removidos,
            "recebidas_removidas": recebidas_removidas,
        }


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
