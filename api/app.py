"""Aplicação FastAPI para análise, interface Web e exportação de lotes fiscais (`api/app.py`)."""

from __future__ import annotations

import math
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from api import __version__
from models.fiscal import ResultadoAnalise
from models.normalizadores import DECIMAL_ZERO, limpar_chave_acesso
from parsers.detector import extrair_xmls_de_zip, processar_arquivo_xml
from parsers.xml_seguro import ErroParseXML
from reports.assistente_lote import (
    ErroComunicacaoLLM,
    ErroLimiteUsoLLM,
    ErroLLMNaoConfigurado,
    ServicoAssistenteLote,
)
from reports.danfe_svg import montar_contexto_danfe, montar_contexto_danfse
from reports.exportacao import exportar_itens_csv, exportar_lote_xlsx, exportar_notas_csv
from reports.resumo import ResumoLote, gerar_resumo_lote
from sefaz.cert_a1 import (
    ConfigCertificadoSEFAZ,
    ErroCertificadoA1,
    MetadadosCertificado,
    atualizar_config_sefaz_local,
    carregar_certificado_configurado,
    ler_config_sefaz_local,
    remover_certificado_local,
    resolver_diretorio_certificado,
    salvar_certificado_local,
)
from sefaz.controle_nsu import (
    CSTAT_DOCS_LOCALIZADOS,
    ErroBloqueioSEFAZ,
    EstadoCursorNSU,
    verificar_janela_consulta,
)
from sefaz.descompactador import compactar_xml_para_doc_zip_b64
from sefaz.dist_dfe import (
    ClienteNFeDistribuicaoDFe,
    ErroComunicacaoSEFAZ,
    ItemDocZip,
    RespostaDistDFe,
    resolver_ambiente_sefaz,
)
from sefaz.storage import (
    RegistroNotaRecebida,
    RegistroSyncLog,
    RepositorioDistDFeSQLite,
    ResumoExecucaoSync,
    ResumoHistoricoLote,
    sincronizar_dist_dfe,
)
from validators.lote import criar_resultado_erro_xml, validar_lote

MAX_LOTES_MEMORIA = 20
_EXTENSOES_PERMITIDAS = (".xml", ".zip")
_EXTENSOES_CERTIFICADO = (".pfx", ".p12")
_ZIP_MAGIC_PREFIX = b"PK\x03\x04"

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
PASTA_WEB_STATIC = RAIZ_PROJETO / "web" / "static"
PASTA_WEB_TEMPLATES = RAIZ_PROJETO / "web" / "templates"
PASTA_FIXTURES_DEMO = RAIZ_PROJETO / "tests" / "fixtures"

_ARQUIVOS_DEMO = (
    "nfe_55_valida.xml",
    "nfe_55_cnpj_alfanumerico.xml",
    "nfce_65_valida.xml",
    "nfse_nacional_valida.xml",
    "nfe_55_homologacao.xml",
    "nfe_55_sem_protocolo.xml",
    "nfe_55_soma_errada.xml",
    "nfe_resumo.xml",
)

_ITENS_DEMO_RECEBIDAS: tuple[tuple[str, str, str], ...] = (
    ("000000000000001", "procNFe_v4.00.xsd", "nfe_55_valida.xml"),
    ("000000000000002", "procNFe_v4.00.xsd", "nfe_55_cnpj_alfanumerico.xml"),
    ("000000000000003", "resNFe_v1.01.xsd", "nfe_resumo.xml"),
    ("000000000000004", "procEventoNFe_v1.00.xsd", "nfe_evento_cancelamento.xml"),
)


@dataclass(slots=True)
class LoteArmazenado:
    """Representa um lote processado mantido em memória (cache LRU)."""

    lote_id: str
    criado_em: datetime
    resumo: ResumoLote
    resultados: list[ResultadoAnalise]
    xmls_brutos: dict[str, bytes]


class RespostaLote(BaseModel):
    """Payload de resposta dos endpoints de criação e consulta de lote."""

    lote_id: str
    criado_em: datetime
    resumo: ResumoLote
    resultados: list[ResultadoAnalise]


class StatusLLMResponse(BaseModel):
    """Payload de status do assistente opcional com LLM."""

    configurado: bool
    modelo: str
    chamadas_restantes: int
    max_chamadas_hora: int


class PerguntaLoteRequest(BaseModel):
    """Payload de entrada para perguntas sobre um lote processado."""

    model_config = ConfigDict(str_strip_whitespace=True)

    pergunta: str = Field(..., min_length=3, max_length=500)


class PerguntaLoteResponse(BaseModel):
    """Payload de resposta do assistente LLM sobre um lote."""

    lote_id: str
    pergunta: str
    resposta: str
    em_cache: bool
    modelo: str
    chamadas_restantes: int


class StatusRecebidasSEFAZ(BaseModel):
    """Payload de status do Certificado Digital A1, cursor NSU e logs `DistDFe`."""

    certificado_configurado: bool
    certificado: MetadadosCertificado | None = None
    erro_certificado: str | None = None
    uf_autor: str = "35"
    ambiente: Literal[1, 2] = 2
    origem_certificado: Literal["interface", "env", "nao_configurado"] = "nao_configurado"
    config: ConfigCertificadoSEFAZ = Field(default_factory=ConfigCertificadoSEFAZ)
    cursor: EstadoCursorNSU
    bloqueado: bool = False
    segundos_bloqueio_restantes: int = 0
    ultimos_logs: list[RegistroSyncLog] = Field(default_factory=list)


class AtualizarConfigSEFAZRequest(BaseModel):
    """Payload para atualização rápida de UF autora e/ou ambiente SEFAZ (1 ou 2)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    uf_autor: str | None = None
    ambiente: Literal[1, 2] | None = None


class ListaNotasRecebidasResponse(BaseModel):
    """Payload de listagem filtrável de notas recebidas via `NFeDistribuicaoDFe`."""

    total: int
    valor_total_filtrado: Decimal
    notas: list[RegistroNotaRecebida] = Field(default_factory=list)


class ListaHistoricoLotesResponse(BaseModel):
    """Payload de listagem do histórico de lotes persistidos no SQLite."""

    total: int
    lotes: list[ResumoHistoricoLote] = Field(default_factory=list)


class RepositorioLotesMemoria:
    """Repositório thread-safe em memória com política de descarte LRU."""

    def __init__(self, max_lotes: int = MAX_LOTES_MEMORIA) -> None:
        self._max_lotes = max(1, max_lotes)
        self._lotes: OrderedDict[str, LoteArmazenado] = OrderedDict()
        self._lock = threading.Lock()

    def criar_e_salvar(
        self,
        resultados: list[ResultadoAnalise],
        xmls_brutos: dict[str, bytes],
    ) -> LoteArmazenado:
        """Consolida o resumo do lote, armazena no cache LRU e retorna `LoteArmazenado`."""
        lote_id = uuid.uuid4().hex[:12]
        criado_em = datetime.now(UTC)
        resumo = gerar_resumo_lote(resultados)
        lote = LoteArmazenado(
            lote_id=lote_id,
            criado_em=criado_em,
            resumo=resumo,
            resultados=resultados,
            xmls_brutos=xmls_brutos,
        )
        return self.salvar_lote(lote)

    def salvar_lote(self, lote: LoteArmazenado) -> LoteArmazenado:
        """Insere ou atualiza um `LoteArmazenado` existente no cache LRU."""
        with self._lock:
            self._lotes[lote.lote_id] = lote
            self._lotes.move_to_end(lote.lote_id)
            while len(self._lotes) > self._max_lotes:
                self._lotes.popitem(last=False)
        return lote

    def obter(self, lote_id: str) -> LoteArmazenado | None:
        """Busca um lote pelo ID e atualiza sua posição de acesso no LRU."""
        with self._lock:
            lote = self._lotes.get(lote_id)
            if lote is not None:
                self._lotes.move_to_end(lote_id)
            return lote

    def remover(self, lote_id: str) -> bool:
        """Remove um lote específico da memória. Retorna `True` se existia."""
        with self._lock:
            return self._lotes.pop(lote_id, None) is not None

    def limpar(self) -> None:
        """Remove todos os lotes armazenados em memória."""
        with self._lock:
            self._lotes.clear()


_REPOSITORIO_LOTES = RepositorioLotesMemoria()
_SERVICO_ASSISTENTE = ServicoAssistenteLote()
_REPOSITORIO_RECEBIDAS: RepositorioDistDFeSQLite | None = None
_CONSULTOR_DIST_DFE: Callable[[str], RespostaDistDFe] | None = None
_DIRETORIO_CERTIFICADO: Path | None = None


def obter_repositorio_lotes() -> RepositorioLotesMemoria:
    """Retorna a instância singleton do repositório de lotes em memória."""
    return _REPOSITORIO_LOTES


def obter_servico_assistente() -> ServicoAssistenteLote:
    """Retorna a instância atual do serviço de assistente LLM."""
    return _SERVICO_ASSISTENTE


def definir_servico_assistente(servico: ServicoAssistenteLote) -> None:
    """Substitui a instância do serviço de assistente LLM (útil em testes offline)."""
    global _SERVICO_ASSISTENTE
    _SERVICO_ASSISTENTE = servico


def obter_repositorio_recebidas() -> RepositorioDistDFeSQLite:
    """Retorna o repositório SQLite de notas recebidas (`DistDFe`), inicializando sob demanda."""
    global _REPOSITORIO_RECEBIDAS
    if _REPOSITORIO_RECEBIDAS is None:
        _REPOSITORIO_RECEBIDAS = RepositorioDistDFeSQLite()
    return _REPOSITORIO_RECEBIDAS


def definir_repositorio_recebidas(repo: RepositorioDistDFeSQLite | None) -> None:
    """Substitui o repositório SQLite de notas recebidas (útil em testes com `tmp_path`)."""
    global _REPOSITORIO_RECEBIDAS
    _REPOSITORIO_RECEBIDAS = repo


def definir_consultor_dist_dfe(fn: Callable[[str], RespostaDistDFe] | None) -> None:
    """Injeta uma função `(ult_nsu) -> RespostaDistDFe` para testes offline de sincronização."""
    global _CONSULTOR_DIST_DFE
    _CONSULTOR_DIST_DFE = fn


def obter_diretorio_certificado() -> Path:
    """Retorna o diretório configurado para armazenamento local do certificado A1."""
    return resolver_diretorio_certificado(_DIRETORIO_CERTIFICADO)


def definir_diretorio_certificado(caminho: str | Path | None) -> None:
    """Substitui o diretório local do certificado A1 (útil em testes com `tmp_path`)."""
    global _DIRETORIO_CERTIFICADO
    _DIRETORIO_CERTIFICADO = Path(caminho) if caminho is not None else None


def processar_entradas_com_xml_bruto(
    entradas: Iterable[tuple[str, bytes]],
) -> tuple[list[ResultadoAnalise], dict[str, bytes]]:
    """Processa entradas `(nome, bytes)` extraindo ZIPs e indexando os XMLs brutos."""
    pares: list[tuple[ResultadoAnalise, bytes]] = []

    for nome_arquivo, dados_bytes in entradas:
        eh_zip = nome_arquivo.lower().endswith(".zip") or dados_bytes.startswith(_ZIP_MAGIC_PREFIX)
        if eh_zip:
            try:
                for nome_interno, xml_bytes in extrair_xmls_de_zip(dados_bytes):
                    origem = f"{nome_arquivo}:{nome_interno}"
                    res = processar_arquivo_xml(xml_bytes, origem)
                    pares.append((res, xml_bytes))
            except ErroParseXML as exc:
                res_erro = criar_resultado_erro_xml(
                    nome_arquivo,
                    str(exc),
                    codigo="ZIP_INVALIDO",
                )
                pares.append((res_erro, b""))
        else:
            res = processar_arquivo_xml(dados_bytes, nome_arquivo)
            pares.append((res, dados_bytes))

    resultados_validados = validar_lote([r for r, _ in pares])
    xmls_brutos: dict[str, bytes] = {}

    for idx, (res, (_, xml_bytes)) in enumerate(zip(resultados_validados, pares, strict=True)):
        if not xml_bytes:
            continue
        xmls_brutos[str(idx)] = xml_bytes
        doc = res.documento
        if doc is not None and doc.chave_acesso:
            chave_orig = doc.chave_acesso
            chave_limpa = limpar_chave_acesso(chave_orig)
            if chave_orig not in xmls_brutos:
                xmls_brutos[chave_orig] = xml_bytes
            if chave_limpa and chave_limpa not in xmls_brutos:
                xmls_brutos[chave_limpa] = xml_bytes

    return resultados_validados, xmls_brutos


def _filtrar_resultados(
    resultados: list[ResultadoAnalise],
    *,
    tipo: str | None = None,
    valido: bool | None = None,
    busca: str | None = None,
) -> list[ResultadoAnalise]:
    filtrados = resultados

    if tipo is not None and tipo.strip():
        tipo_alvo = tipo.strip().upper()
        filtrados = [
            r
            for r in filtrados
            if (r.documento.tipo if r.documento is not None else "ERRO_XML") == tipo_alvo
        ]

    if valido is not None:
        filtrados = [r for r in filtrados if r.valido == valido]

    if busca is not None and busca.strip():
        termo = busca.strip().lower()

        def _combina_busca(res: ResultadoAnalise) -> bool:
            if termo in res.arquivo_origem.lower():
                return True
            doc = res.documento
            if doc is None:
                return bool(res.erro_fatal and termo in res.erro_fatal.lower())
            campos = [
                doc.chave_acesso,
                limpar_chave_acesso(doc.chave_acesso),
                doc.numero,
                doc.serie,
                doc.emitente.documento,
                doc.emitente.razao_social,
                doc.emitente.nome_fantasia or "",
            ]
            if doc.destinatario is not None:
                campos.extend(
                    [
                        doc.destinatario.documento,
                        doc.destinatario.razao_social,
                        doc.destinatario.nome_fantasia or "",
                    ]
                )
            return any(termo in c.lower() for c in campos if c)

        filtrados = [r for r in filtrados if _combina_busca(r)]

    return filtrados


def _gerar_nome_rotulo_upload(nomes_enviados: list[str], total_resultados: int) -> str:
    """Gera um rótulo amigável para o histórico de lotes com base nos arquivos enviados."""
    nomes_limpos = [n.strip() for n in nomes_enviados if n.strip()]
    if not nomes_limpos:
        qtd = total_resultados
        plural = "arquivo" if qtd == 1 else "arquivos"
        return f"{qtd} {plural}"

    if len(nomes_limpos) == 1:
        nome_unico = nomes_limpos[0]
        if nome_unico.lower().endswith(".zip") and total_resultados > 1:
            return f"{nome_unico} ({total_resultados} XMLs)"
        return f"1 arquivo ({nome_unico})"

    amostra = ", ".join(nomes_limpos[:2])
    if len(nomes_limpos) > 2:
        amostra = f"{amostra}, ..."
    return f"{len(nomes_limpos)} arquivos ({amostra})"


def _resolver_lote_ou_acervo(lote_id: str) -> LoteArmazenado:
    """Resolve `acervo` dinamicamente no SQLite ou busca `lote_id` em RAM com fallback SQLite."""
    ident = lote_id.strip()
    if ident.lower() == "acervo":
        criado_em, resumo, resultados, xmls_brutos = (
            obter_repositorio_recebidas().obter_acervo_unificado()
        )
        return LoteArmazenado(
            lote_id="acervo",
            criado_em=criado_em,
            resumo=resumo,
            resultados=resultados,
            xmls_brutos=xmls_brutos,
        )

    lote = _REPOSITORIO_LOTES.obter(ident)
    if lote is not None:
        return lote

    persistido = obter_repositorio_recebidas().obter_lote_upload(ident)
    if persistido is None:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")

    criado_em, resumo, resultados, xmls_brutos = persistido
    lote_hidratado = LoteArmazenado(
        lote_id=ident,
        criado_em=criado_em,
        resumo=resumo,
        resultados=resultados,
        xmls_brutos=xmls_brutos,
    )
    return _REPOSITORIO_LOTES.salvar_lote(lote_hidratado)


def _localizar_nota_no_lote(
    lote: LoteArmazenado, identificador: str
) -> tuple[int, ResultadoAnalise]:
    ident_strip = identificador.strip()
    if ident_strip.isdigit() and len(ident_strip) < 44:
        indice = int(ident_strip)
        if 0 <= indice < len(lote.resultados):
            return indice, lote.resultados[indice]

    chave_alvo = limpar_chave_acesso(ident_strip)
    for idx, resultado in enumerate(lote.resultados):
        doc = resultado.documento
        if doc is None or not doc.chave_acesso:
            continue
        if doc.chave_acesso == ident_strip or (
            chave_alvo and limpar_chave_acesso(doc.chave_acesso) == chave_alvo
        ):
            return idx, resultado

    raise HTTPException(
        status_code=404,
        detail=f"Nota '{identificador}' não encontrada no lote '{lote.lote_id}'.",
    )


app = FastAPI(
    title="nfe-local",
    description=(
        "Visualizador, validador e analisador local em lote de NF-e (55), "
        "NFC-e (65) e NFS-e Padrão Nacional."
    ),
    version=__version__,
)

app.mount("/static", StaticFiles(directory=str(PASTA_WEB_STATIC)), name="static")
templates = Jinja2Templates(directory=str(PASTA_WEB_TEMPLATES))


@app.get("/", response_class=HTMLResponse)
def pagina_inicial(request: Request) -> HTMLResponse:
    """Renderiza a interface web principal do `nfe-local`."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {"version": __version__},
    )


@app.get("/api/health")
def health_check() -> dict[str, str]:
    """Verifica a disponibilidade e a versão da API."""
    return {"status": "ok", "version": __version__}


@app.post("/api/lote", response_model=RespostaLote)
async def criar_lote(
    files: list[UploadFile] = File(...),  # noqa: B008
) -> RespostaLote:
    """Recebe upload de múltiplos arquivos `.xml` e/ou `.zip`, valida e consolida o lote."""
    if not files:
        raise HTTPException(
            status_code=400,
            detail="Nenhum arquivo enviado. Envie ao menos um arquivo .xml ou .zip.",
        )

    entradas: list[tuple[str, bytes]] = []
    for upload in files:
        nome = (upload.filename or "").strip()
        if not nome or not nome.lower().endswith(_EXTENSOES_PERMITIDAS):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Arquivo '{nome or 'sem_nome'}' possui extensão inválida. "
                    "Apenas arquivos .xml e .zip são permitidos."
                ),
            )
        conteudo = await upload.read()
        entradas.append((nome, conteudo))

    resultados, xmls_brutos = processar_entradas_com_xml_bruto(entradas)
    lote = _REPOSITORIO_LOTES.criar_e_salvar(resultados, xmls_brutos)
    nome_rotulo = _gerar_nome_rotulo_upload([n for n, _ in entradas], len(resultados))
    obter_repositorio_recebidas().salvar_lote_upload(
        lote_id=lote.lote_id,
        criado_em=lote.criado_em,
        resumo=lote.resumo,
        resultados=lote.resultados,
        xmls_brutos=lote.xmls_brutos,
        nome_rotulo=nome_rotulo,
        origem="upload",
    )

    return RespostaLote(
        lote_id=lote.lote_id,
        criado_em=lote.criado_em,
        resumo=lote.resumo,
        resultados=lote.resultados,
    )


@app.post("/api/lote/demo", response_model=RespostaLote)
def criar_lote_demo() -> RespostaLote:
    """Carrega um lote de demonstração a partir dos XMLs sintéticos em `tests/fixtures/`."""
    entradas: list[tuple[str, bytes]] = []
    for nome_arq in _ARQUIVOS_DEMO:
        caminho = PASTA_FIXTURES_DEMO / nome_arq
        if caminho.is_file():
            entradas.append((nome_arq, caminho.read_bytes()))

    if not entradas:
        raise HTTPException(
            status_code=404,
            detail="Arquivos sintéticos de demonstração não encontrados.",
        )

    resultados, xmls_brutos = processar_entradas_com_xml_bruto(entradas)
    lote = _REPOSITORIO_LOTES.criar_e_salvar(resultados, xmls_brutos)
    obter_repositorio_recebidas().salvar_lote_upload(
        lote_id=lote.lote_id,
        criado_em=lote.criado_em,
        resumo=lote.resumo,
        resultados=lote.resultados,
        xmls_brutos=lote.xmls_brutos,
        nome_rotulo="Lote de Demonstração",
        origem="demo",
    )

    return RespostaLote(
        lote_id=lote.lote_id,
        criado_em=lote.criado_em,
        resumo=lote.resumo,
        resultados=lote.resultados,
    )


@app.get("/api/acervo", response_model=RespostaLote)
def obter_acervo(
    tipo: str | None = Query(default=None, description="Filtra por tipo (NFE, NFCE, NFSE, etc.)"),
    valido: bool | None = Query(default=None, description="Filtra por status de validade"),
    busca: str | None = Query(
        default=None,
        description="Busca textual por chave, número, CNPJ/CPF ou razão social",
    ),
) -> RespostaLote:
    """Retorna o resumo e todas as notas consolidadas no Acervo Unificado (`lote_id='acervo'`)."""
    return obter_lote(
        lote_id="acervo",
        tipo=tipo,
        valido=valido,
        busca=busca,
    )


@app.get("/api/lotes", response_model=ListaHistoricoLotesResponse)
def listar_historico_lotes(
    limite: int = Query(default=50, ge=1, le=500, description="Limite máximo de lotes listados"),
) -> ListaHistoricoLotesResponse:
    """Lista o histórico de lotes salvos no SQLite ordenados do mais recente ao mais antigo."""
    lotes = obter_repositorio_recebidas().listar_lotes_upload(limite=limite)
    return ListaHistoricoLotesResponse(total=len(lotes), lotes=lotes)


@app.delete("/api/lote/{lote_id}")
def excluir_lote(lote_id: str) -> dict[str, str | bool]:
    """Remove um lote específico do SQLite e do cache em memória."""
    removido_db = obter_repositorio_recebidas().excluir_lote_upload(lote_id)
    removido_mem = _REPOSITORIO_LOTES.remover(lote_id)
    if not removido_db and not removido_mem:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")
    return {"lote_id": lote_id, "removido": True}


@app.delete("/api/lotes")
def limpar_historico_lotes(
    apenas_demo: bool = Query(
        default=False,
        description="Se true, remove apenas os lotes de demonstração mantendo uploads reais.",
    ),
) -> dict[str, int]:
    """Limpa o histórico de lotes do SQLite (e do cache em memória)."""
    repo_db = obter_repositorio_recebidas()
    if apenas_demo:
        ids_demo = [
            item.lote_id
            for item in repo_db.listar_lotes_upload(limite=1000)
            if item.origem == "demo"
        ]
        removidos = repo_db.limpar_lotes_upload(apenas_demo=True)
        for lid in ids_demo:
            _REPOSITORIO_LOTES.remover(lid)
    else:
        removidos = repo_db.limpar_lotes_upload(apenas_demo=False)
        _REPOSITORIO_LOTES.limpar()
    return {"removidos": removidos}


@app.get("/api/lote/{lote_id}", response_model=RespostaLote)
def obter_lote(
    lote_id: str,
    tipo: str | None = Query(default=None, description="Filtra por tipo (NFE, NFCE, NFSE, etc.)"),
    valido: bool | None = Query(default=None, description="Filtra por status de validade"),
    busca: str | None = Query(
        default=None,
        description="Busca textual por chave, número, CNPJ/CPF ou razão social",
    ),
) -> RespostaLote:
    """Retorna o resumo e os documentos de um lote processado, com suporte a filtros."""
    lote = _resolver_lote_ou_acervo(lote_id)

    resultados_filtrados = _filtrar_resultados(
        lote.resultados,
        tipo=tipo,
        valido=valido,
        busca=busca,
    )
    return RespostaLote(
        lote_id=lote.lote_id,
        criado_em=lote.criado_em,
        resumo=lote.resumo,
        resultados=resultados_filtrados,
    )


@app.get("/api/lote/{lote_id}/nota/{identificador}", response_model=ResultadoAnalise)
def obter_nota_do_lote(lote_id: str, identificador: str) -> ResultadoAnalise:
    """Retorna o `ResultadoAnalise` de uma nota específica pela chave de acesso ou índice."""
    lote = _resolver_lote_ou_acervo(lote_id)

    _, resultado = _localizar_nota_no_lote(lote, identificador)
    return resultado


@app.get("/api/lote/{lote_id}/exportar")
def exportar_lote(
    lote_id: str,
    formato: Literal["csv", "itens_csv", "xlsx"] = Query(default="xlsx"),
) -> Response:
    """Exporta o lote analisado para download nos formatos CSV (notas/itens) ou XLSX."""
    lote = _resolver_lote_ou_acervo(lote_id)

    if formato == "csv":
        conteudo = exportar_notas_csv(lote.resultados)
        media_type = "text/csv; charset=utf-8"
        nome_arquivo = f"lote_{lote.lote_id}.csv"
    elif formato == "itens_csv":
        conteudo = exportar_itens_csv(lote.resultados)
        media_type = "text/csv; charset=utf-8"
        nome_arquivo = f"lote_{lote.lote_id}_itens.csv"
    else:
        conteudo = exportar_lote_xlsx(lote.resultados, resumo=lote.resumo)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        nome_arquivo = f"lote_{lote.lote_id}.xlsx"

    return Response(
        content=conteudo,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
    )


@app.get("/lote/{lote_id}/nota/{identificador}/espelho", response_class=HTMLResponse)
def renderizar_espelho_nota(
    request: Request,
    lote_id: str,
    identificador: str,
) -> HTMLResponse:
    """Renderiza o espelho A4 imprimível (DANFE NF-e/NFC-e ou DANFSe NT 008/2026)."""
    lote = _resolver_lote_ou_acervo(lote_id)

    _, resultado = _localizar_nota_no_lote(lote, identificador)
    if resultado.documento is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Não é possível gerar espelho DANFE/DANFSe para arquivo com erro fatal "
                "de XML ou layout desconhecido."
            ),
        )

    if resultado.documento.tipo == "NFSE":
        contexto = montar_contexto_danfse(resultado)
        nome_template = "danfse.html"
    else:
        contexto = montar_contexto_danfe(resultado)
        nome_template = "danfe.html"

    contexto["lote_id"] = lote.lote_id
    contexto["identificador"] = identificador
    return templates.TemplateResponse(request, nome_template, contexto)


@app.get("/lote/{lote_id}/nota/{identificador}/xml")
def baixar_xml_nota(lote_id: str, identificador: str) -> Response:
    """Retorna os bytes brutos do XML original da nota selecionada."""
    lote = _resolver_lote_ou_acervo(lote_id)

    indice, resultado = _localizar_nota_no_lote(lote, identificador)
    xml_bytes = (
        lote.xmls_brutos.get(identificador.strip())
        or lote.xmls_brutos.get(limpar_chave_acesso(identificador))
        or lote.xmls_brutos.get(str(indice))
    )
    if not xml_bytes:
        raise HTTPException(
            status_code=404,
            detail="Conteúdo bruto do XML não disponível para este item.",
        )

    doc = resultado.documento
    base_nome = doc.chave_acesso if (doc and doc.chave_acesso) else resultado.arquivo_origem
    nome_seguro = re.sub(r"[^0-9A-Za-z._-]", "_", base_nome)
    if not nome_seguro.lower().endswith(".xml"):
        nome_seguro = f"{nome_seguro}.xml"

    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{nome_seguro}"'},
    )


@app.get("/api/llm/status", response_model=StatusLLMResponse)
def status_assistente_llm() -> StatusLLMResponse:
    """Retorna a disponibilidade, modelo e franquia restante do assistente LLM opcional."""
    dados = _SERVICO_ASSISTENTE.obter_status()
    return StatusLLMResponse(**dados)


@app.post("/api/lote/{lote_id}/perguntar", response_model=PerguntaLoteResponse)
def perguntar_sobre_lote(lote_id: str, payload: PerguntaLoteRequest) -> PerguntaLoteResponse:
    """Consulta o assistente LLM opcional sobre o resumo e alertas de um lote processado."""
    lote = _resolver_lote_ou_acervo(lote_id)

    try:
        resultado_llm = _SERVICO_ASSISTENTE.perguntar(
            resumo=lote.resumo,
            resultados=lote.resultados,
            pergunta=payload.pergunta,
        )
    except ErroLLMNaoConfigurado as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ErroLimiteUsoLLM as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ErroComunicacaoLLM as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return PerguntaLoteResponse(
        lote_id=lote.lote_id,
        pergunta=resultado_llm.pergunta,
        resposta=resultado_llm.resposta,
        em_cache=resultado_llm.em_cache,
        modelo=resultado_llm.modelo,
        chamadas_restantes=resultado_llm.chamadas_restantes,
    )


def _calcular_estado_bloqueio(cursor: EstadoCursorNSU) -> tuple[bool, int]:
    """Retorna `(bloqueado, segundos_restantes)` a partir de `cursor.bloqueado_ate`."""
    if cursor.bloqueado_ate is None:
        return False, 0

    agora = datetime.now(UTC)
    alvo = (
        cursor.bloqueado_ate.replace(tzinfo=UTC)
        if cursor.bloqueado_ate.tzinfo is None
        else cursor.bloqueado_ate.astimezone(UTC)
    )
    if alvo <= agora:
        return False, 0

    segundos = max(1, int(math.ceil((alvo - agora).total_seconds())))
    return True, segundos


def _montar_status_recebidas_sefaz(ambiente_override: int | None = None) -> StatusRecebidasSEFAZ:
    """Inspeciona certificado A1, configuração local, cursor `ultNSU` e trava `656`."""
    cert_dir = obter_diretorio_certificado()
    config_sefaz = ler_config_sefaz_local(cert_dir)

    cert_configurado = False
    metadados_cert: MetadadosCertificado | None = None
    erro_cert: str | None = None
    try:
        _, _, metadados_cert, config_sefaz = carregar_certificado_configurado(cert_dir)
        cert_configurado = True
    except ErroCertificadoA1 as exc:
        erro_cert = str(exc)

    amb: Literal[1, 2] = (
        resolver_ambiente_sefaz(ambiente_override)
        if ambiente_override is not None
        else config_sefaz.ambiente
    )
    repo = obter_repositorio_recebidas()
    cursor = repo.obter_cursor(ambiente=amb)
    bloqueado, segundos_restantes = _calcular_estado_bloqueio(cursor)
    ultimos_logs = repo.listar_logs_sync(limite=5)

    return StatusRecebidasSEFAZ(
        certificado_configurado=cert_configurado,
        certificado=metadados_cert,
        erro_certificado=erro_cert,
        uf_autor=config_sefaz.uf_autor,
        ambiente=amb,
        origem_certificado=config_sefaz.origem,
        config=config_sefaz,
        cursor=cursor,
        bloqueado=bloqueado,
        segundos_bloqueio_restantes=segundos_restantes,
        ultimos_logs=ultimos_logs,
    )


@app.get("/api/recebidas/status", response_model=StatusRecebidasSEFAZ)
def status_recebidas_sefaz(
    ambiente: int | None = Query(
        default=None, description="Ambiente SEFAZ (1=Produção, 2=Homologação)"
    ),
) -> StatusRecebidasSEFAZ:
    """Inspeciona certificado A1, cursor `ultNSU`/`maxNSU`, trava `656` e últimos logs `DistDFe`."""
    return _montar_status_recebidas_sefaz(ambiente_override=ambiente)


@app.post("/api/recebidas/certificado", response_model=StatusRecebidasSEFAZ)
async def cadastrar_certificado_recebidas(
    arquivo: UploadFile = File(...),  # noqa: B008
    senha: str = Form(...),
    uf_autor: str = Form("35"),
    ambiente: int = Form(2),
) -> StatusRecebidasSEFAZ:
    """Valida e salva localmente o Certificado Digital A1 (`.pfx`/`.p12`), senha, UF e ambiente."""
    nome_original = (arquivo.filename or "").strip()
    if not nome_original or not nome_original.lower().endswith(_EXTENSOES_CERTIFICADO):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Arquivo '{nome_original or 'sem_nome'}' inválido. "
                "Envie um Certificado Digital A1 com extensão .pfx ou .p12."
            ),
        )

    pfx_bytes = await arquivo.read()
    amb_norm: Literal[1, 2] = 1 if ambiente == 1 else (2 if ambiente == 2 else 0)  # type: ignore[assignment]
    if amb_norm not in (1, 2):
        raise HTTPException(
            status_code=400,
            detail="Ambiente SEFAZ inválido (informe 1 para Produção ou 2 para Homologação).",
        )

    try:
        salvar_certificado_local(
            pfx_bytes=pfx_bytes,
            senha=senha,
            uf_autor=uf_autor,
            ambiente=amb_norm,
            cert_dir=obter_diretorio_certificado(),
        )
    except ErroCertificadoA1 as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _montar_status_recebidas_sefaz()


@app.patch("/api/recebidas/config", response_model=StatusRecebidasSEFAZ)
def atualizar_config_recebidas(payload: AtualizarConfigSEFAZRequest) -> StatusRecebidasSEFAZ:
    """Alterna o ambiente SEFAZ (1=Produção, 2=Homologação) e/ou UF autora sem reenviar o `.pfx`."""
    try:
        atualizar_config_sefaz_local(
            uf_autor=payload.uf_autor,
            ambiente=payload.ambiente,
            cert_dir=obter_diretorio_certificado(),
        )
    except ErroCertificadoA1 as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _montar_status_recebidas_sefaz()


@app.delete("/api/recebidas/certificado", response_model=StatusRecebidasSEFAZ)
def remover_certificado_recebidas() -> StatusRecebidasSEFAZ:
    """Remove o Certificado Digital A1 salvo localmente no computador."""
    remover_certificado_local(cert_dir=obter_diretorio_certificado())
    return _montar_status_recebidas_sefaz()


@app.post("/api/recebidas/sincronizar", response_model=ResumoExecucaoSync)
def sincronizar_recebidas_sefaz(
    ambiente: int | None = Query(
        default=None, description="Ambiente SEFAZ (1=Produção, 2=Homologação)"
    ),
) -> ResumoExecucaoSync:
    """Executa uma rodada de sincronização `DistDFe` respeitando a trava de 1h (`656`)."""
    cert_dir = obter_diretorio_certificado()
    config_local = ler_config_sefaz_local(cert_dir)
    amb: Literal[1, 2] = (
        resolver_ambiente_sefaz(ambiente) if ambiente is not None else config_local.ambiente
    )
    repo = obter_repositorio_recebidas()
    cursor_atual = repo.obter_cursor(ambiente=amb)

    try:
        verificar_janela_consulta(cursor_atual)
    except ErroBloqueioSEFAZ as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    consultar_fn = _CONSULTOR_DIST_DFE
    if consultar_fn is None:
        try:
            pfx_bytes, senha, _, config_cert = carregar_certificado_configurado(cert_dir)
        except ErroCertificadoA1 as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if ambiente is None:
            amb = config_cert.ambiente

        cliente = ClienteNFeDistribuicaoDFe(
            pfx_bytes=pfx_bytes,
            senha=senha,
            uf_autor=config_cert.uf_autor,
            ambiente=amb,
        )
        consultar_fn = lambda ult_nsu: cliente.consultar_dist_nsu(ult_nsu=ult_nsu)  # noqa: E731

    try:
        return sincronizar_dist_dfe(
            consultar_fn=consultar_fn,
            repo=repo,
            ambiente=amb,
        )
    except ErroBloqueioSEFAZ as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ErroComunicacaoSEFAZ as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/recebidas/demo", response_model=ResumoExecucaoSync)
def carregar_recebidas_demo(
    ambiente: int | None = Query(
        default=None, description="Ambiente SEFAZ (1=Produção, 2=Homologação)"
    ),
) -> ResumoExecucaoSync:
    """Popula o banco SQLite local com um lote sintético `DistDFe` (`procNFe`/`resNFe`/evento)."""
    amb = resolver_ambiente_sefaz(ambiente)
    repo = obter_repositorio_recebidas()

    itens_doc_zip: list[ItemDocZip] = []
    for nsu_str, schema_xml, nome_arquivo in _ITENS_DEMO_RECEBIDAS:
        caminho = PASTA_FIXTURES_DEMO / nome_arquivo
        if not caminho.is_file():
            continue
        xml_bytes = caminho.read_bytes()
        itens_doc_zip.append(
            ItemDocZip(
                nsu=nsu_str,
                schema_xml=schema_xml,
                conteudo_base64=compactar_xml_para_doc_zip_b64(xml_bytes),
            )
        )

    if not itens_doc_zip:
        raise HTTPException(
            status_code=404,
            detail="Fixtures sintéticas de demonstração DistDFe não encontradas.",
        )

    ultimo_nsu_demo = itens_doc_zip[-1].nsu
    # Reinicia cursor de demonstração do ambiente para permitir recarga limpa com 1 clique
    repo.salvar_cursor(EstadoCursorNSU(ambiente=amb))

    def _consultor_demo(_ult_nsu: str) -> RespostaDistDFe:
        return RespostaDistDFe(
            ambiente=amb,
            ver_aplic="DEMO-LOCAL-1.0",
            cstat=CSTAT_DOCS_LOCALIZADOS,
            xmotivo="Documento(s) localizado(s) (Demonstracao Local)",
            dh_resp=datetime.now(UTC),
            ult_nsu=ultimo_nsu_demo,
            max_nsu=ultimo_nsu_demo,
            lote_doc_zip=itens_doc_zip,
        )

    return sincronizar_dist_dfe(
        consultar_fn=_consultor_demo,
        repo=repo,
        ambiente=amb,
        max_paginas=1,
    )


@app.get("/api/recebidas", response_model=ListaNotasRecebidasResponse)
def listar_recebidas(
    ambiente: int | None = Query(default=None, description="Filtra por ambiente (1 ou 2)"),
    emitente: str | None = Query(
        default=None, description="Filtra por CNPJ ou Razão Social do emitente"
    ),
    data_inicio: str | None = Query(
        default=None, description="Data inicial de emissão (YYYY-MM-DD)"
    ),
    data_fim: str | None = Query(default=None, description="Data final de emissão (YYYY-MM-DD)"),
    valor_min: Decimal | None = Query(  # noqa: B008
        default=None, description="Valor total mínimo da nota"
    ),
    valor_max: Decimal | None = Query(  # noqa: B008
        default=None, description="Valor total máximo da nota"
    ),
    categoria: str | None = Query(
        default=None,
        description="Filtra por categoria (NFE_COMPLETA, RESUMO_NFE, etc.)",
    ),
    situacao: str | None = Query(
        default=None,
        description="Filtra por situação (AUTORIZADA, RESUMO, CANCELADA, etc.)",
    ),
) -> ListaNotasRecebidasResponse:
    """Lista as notas recebidas armazenadas no SQLite com suporte a filtros combinados."""
    repo = obter_repositorio_recebidas()
    notas = repo.listar_notas_recebidas(
        ambiente=ambiente,
        emitente=emitente,
        data_inicio=data_inicio,
        data_fim=data_fim,
        valor_min=valor_min,
        valor_max=valor_max,
        categoria=categoria,
        situacao=situacao,
    )
    soma_valores = sum((n.valor_total for n in notas), DECIMAL_ZERO)
    return ListaNotasRecebidasResponse(
        total=len(notas),
        valor_total_filtrado=soma_valores,
        notas=notas,
    )


@app.get("/recebidas/{chave}/espelho", response_class=HTMLResponse)
def renderizar_espelho_nota_recebida(request: Request, chave: str) -> HTMLResponse:
    """Renderiza o espelho DANFE/DANFSe a partir do XML descompactado no SQLite."""
    chave_limpa = limpar_chave_acesso(chave)
    repo = obter_repositorio_recebidas()
    xml_bytes = repo.obter_xml_nota_recebida(chave_limpa)
    if not xml_bytes:
        raise HTTPException(
            status_code=404,
            detail=f"Nota recebida '{chave}' não encontrada no banco SQLite local.",
        )

    resultado = processar_arquivo_xml(xml_bytes, nome_arquivo=f"{chave_limpa}.xml")
    if resultado.documento is None:
        raise HTTPException(
            status_code=400,
            detail="Não foi possível gerar espelho DANFE para o XML armazenado.",
        )

    if resultado.documento.tipo == "NFSE":
        contexto = montar_contexto_danfse(resultado)
        nome_template = "danfse.html"
    else:
        contexto = montar_contexto_danfe(resultado)
        nome_template = "danfe.html"

    contexto["lote_id"] = "recebidas"
    contexto["identificador"] = chave_limpa
    contexto["url_xml"] = f"/recebidas/{chave_limpa}/xml"
    return templates.TemplateResponse(request, nome_template, contexto)


@app.get("/recebidas/{chave}/xml")
def baixar_xml_nota_recebida(chave: str) -> Response:
    """Faz o download do XML descompactado de uma nota recebida armazenada no SQLite."""
    chave_limpa = limpar_chave_acesso(chave)
    repo = obter_repositorio_recebidas()
    xml_bytes = repo.obter_xml_nota_recebida(chave_limpa)
    if not xml_bytes:
        raise HTTPException(
            status_code=404,
            detail=f"XML da nota recebida '{chave}' não encontrado no banco SQLite local.",
        )

    nome_seguro = re.sub(r"[^0-9A-Za-z._-]", "_", chave_limpa or "nota_recebida")
    if not nome_seguro.lower().endswith(".xml"):
        nome_seguro = f"{nome_seguro}.xml"

    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{nome_seguro}"'},
    )


@app.delete("/api/recebidas")
def limpar_recebidas_sefaz() -> dict[str, int]:
    """Remove notas recebidas, eventos, cursores NSU e logs de sincronização do SQLite local."""
    removidas = obter_repositorio_recebidas().limpar_notas_recebidas(zerar_cursor=True)
    return {"removidas": removidas}


@app.post("/api/sistema/resetar-banco")
def resetar_banco_sistema() -> dict[str, str | int]:
    """Zera completamente o banco SQLite local (Aba 1 + Aba 2), cache de lotes e cache LLM."""
    resultado = obter_repositorio_recebidas().resetar_banco_completo()
    _REPOSITORIO_LOTES.limpar()
    _SERVICO_ASSISTENTE.limpar_cache()
    return {
        "status": "ok",
        "lotes_removidos": resultado["lotes_removidos"],
        "recebidas_removidas": resultado["recebidas_removidas"],
    }
