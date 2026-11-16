"""Aplicação FastAPI para análise, interface Web e exportação de lotes fiscais (`api/app.py`)."""

from __future__ import annotations

import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from api import __version__
from models.fiscal import ResultadoAnalise
from models.normalizadores import limpar_chave_acesso
from parsers.detector import extrair_xmls_de_zip, processar_arquivo_xml
from parsers.xml_seguro import ErroParseXML
from reports.danfe_svg import montar_contexto_danfe, montar_contexto_danfse
from reports.exportacao import exportar_itens_csv, exportar_lote_xlsx, exportar_notas_csv
from reports.resumo import ResumoLote, gerar_resumo_lote
from validators.lote import criar_resultado_erro_xml, validar_lote

MAX_LOTES_MEMORIA = 20
_EXTENSOES_PERMITIDAS = (".xml", ".zip")
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
        with self._lock:
            self._lotes[lote_id] = lote
            self._lotes.move_to_end(lote_id)
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

    def limpar(self) -> None:
        """Remove todos os lotes armazenados em memória."""
        with self._lock:
            self._lotes.clear()


_REPOSITORIO_LOTES = RepositorioLotesMemoria()


def obter_repositorio_lotes() -> RepositorioLotesMemoria:
    """Retorna a instância singleton do repositório de lotes em memória."""
    return _REPOSITORIO_LOTES


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

    return RespostaLote(
        lote_id=lote.lote_id,
        criado_em=lote.criado_em,
        resumo=lote.resumo,
        resultados=lote.resultados,
    )


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
    lote = _REPOSITORIO_LOTES.obter(lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")

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
    lote = _REPOSITORIO_LOTES.obter(lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")

    _, resultado = _localizar_nota_no_lote(lote, identificador)
    return resultado


@app.get("/api/lote/{lote_id}/exportar")
def exportar_lote(
    lote_id: str,
    formato: Literal["csv", "itens_csv", "xlsx"] = Query(default="xlsx"),
) -> Response:
    """Exporta o lote analisado para download nos formatos CSV (notas/itens) ou XLSX."""
    lote = _REPOSITORIO_LOTES.obter(lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")

    if formato == "csv":
        conteudo = exportar_notas_csv(lote.resultados)
        media_type = "text/csv; charset=utf-8"
        nome_arquivo = f"lote_{lote_id}.csv"
    elif formato == "itens_csv":
        conteudo = exportar_itens_csv(lote.resultados)
        media_type = "text/csv; charset=utf-8"
        nome_arquivo = f"lote_{lote_id}_itens.csv"
    else:
        conteudo = exportar_lote_xlsx(lote.resultados, resumo=lote.resumo)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        nome_arquivo = f"lote_{lote_id}.xlsx"

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
    lote = _REPOSITORIO_LOTES.obter(lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")

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

    contexto["lote_id"] = lote_id
    contexto["identificador"] = identificador
    return templates.TemplateResponse(request, nome_template, contexto)


@app.get("/lote/{lote_id}/nota/{identificador}/xml")
def baixar_xml_nota(lote_id: str, identificador: str) -> Response:
    """Retorna os bytes brutos do XML original da nota selecionada."""
    lote = _REPOSITORIO_LOTES.obter(lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail=f"Lote '{lote_id}' não encontrado.")

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
