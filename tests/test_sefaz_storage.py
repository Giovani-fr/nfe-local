"""Testes unitários (100% offline em `tmp_path`) da persistência SQLite (`sefaz/storage.py`)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sefaz.controle_nsu import (
    CSTAT_CONSUMO_INDEVIDO,
    CSTAT_DOCS_LOCALIZADOS,
    EstadoCursorNSU,
)
from sefaz.descompactador import (
    compactar_xml_para_doc_zip_b64,
    processar_item_doc_zip,
)
from sefaz.dist_dfe import ItemDocZip, RespostaDistDFe
from sefaz.storage import (
    RegistroNotaRecebida,
    RegistroSyncLog,
    RepositorioDistDFeSQLite,
    ResumoExecucaoSync,
    sincronizar_dist_dfe,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CHAVE_NFE_1001 = "35260311222333000181550010000010011100010010"
CHAVE_RESUMO_1007 = "35260311222333000181550010000010071100010073"


class TestRepositorioDistDFeSQLite:
    """Testes de persistência SQLite de cursores NSU, notas recebidas, eventos e logs."""

    def test_persistencia_e_leitura_de_cursor_nsu_e_bloqueio_656(self, tmp_path: Path) -> None:
        repo = RepositorioDistDFeSQLite(tmp_path / "teste_dfe.db")

        # Banco recém-criado devolve cursor zerado para o ambiente solicitado
        cursor_inicial = repo.obter_cursor(ambiente=2)
        assert cursor_inicial.ambiente == 2
        assert cursor_inicial.ultimo_nsu == "000000000000000"
        assert cursor_inicial.max_nsu == "000000000000000"
        assert cursor_inicial.bloqueado_ate is None

        agora = datetime(2026, 3, 18, 12, 0, tzinfo=UTC)
        bloqueio = agora + timedelta(hours=1)
        cursor_656 = EstadoCursorNSU(
            ambiente=2,
            ultimo_nsu="000000000000250",
            max_nsu="000000000000300",
            ultima_consulta=agora,
            bloqueado_ate=bloqueio,
            ultimo_cstat=CSTAT_CONSUMO_INDEVIDO,
            ultimo_motivo="Rejeicao: Consumo Indevido",
        )
        repo.salvar_cursor(cursor_656)

        lido = repo.obter_cursor(ambiente=2)
        assert lido.ultimo_nsu == "000000000000250"
        assert lido.max_nsu == "000000000000300"
        assert lido.ultima_consulta == agora
        assert lido.bloqueado_ate == bloqueio
        assert lido.ultimo_cstat == "656"
        assert lido.ultimo_motivo == "Rejeicao: Consumo Indevido"

        # Ambiente 1 continua independente
        assert repo.obter_cursor(ambiente=1).ultimo_nsu == "000000000000000"

    def test_ciclo_res_nfe_upgrade_para_proc_nfe_e_protecao_contra_downgrade(
        self, tmp_path: Path
    ) -> None:
        repo = RepositorioDistDFeSQLite(tmp_path / "teste_upgrade.db")

        # 1. Monta um resNFe para a mesma chave da nfe_55_valida.xml (CHAVE_NFE_1001)
        xml_resumo_1001 = (
            (FIXTURES_DIR / "nfe_resumo.xml")
            .read_bytes()
            .replace(CHAVE_RESUMO_1007.encode("ascii"), CHAVE_NFE_1001.encode("ascii"))
            .replace(b"<vNF>2450.00</vNF>", b"<vNF>1595.00</vNF>")
        )
        doc_resumo = processar_item_doc_zip(
            ItemDocZip(
                nsu="000000000000010",
                schema_xml="resNFe_v1.01.xsd",
                conteudo_base64=compactar_xml_para_doc_zip_b64(xml_resumo_1001),
            )
        )

        status_1 = repo.salvar_documento_processado(doc_resumo, ambiente=2)
        assert status_1 == "NOVA"

        nota_resumo = repo.obter_nota_recebida(CHAVE_NFE_1001)
        assert nota_resumo is not None
        assert nota_resumo.categoria_schema == "RESUMO_NFE"
        assert nota_resumo.manifestacao_necessaria is True
        assert nota_resumo.situacao == "RESUMO"
        assert nota_resumo.destinatario_documento == ""
        assert repo.obter_xml_nota_recebida(CHAVE_NFE_1001) == xml_resumo_1001

        # 2. Chega o procNFe completo (após manifestação do destinatário) no NSU 15
        xml_completo_1001 = (FIXTURES_DIR / "nfe_55_valida.xml").read_bytes()
        doc_completo = processar_item_doc_zip(
            ItemDocZip(
                nsu="000000000000015",
                schema_xml="procNFe_v4.00.xsd",
                conteudo_base64=compactar_xml_para_doc_zip_b64(xml_completo_1001),
            )
        )

        status_2 = repo.salvar_documento_processado(doc_completo, ambiente=2)
        assert status_2 == "ATUALIZADA"

        nota_completa = repo.obter_nota_recebida(CHAVE_NFE_1001)
        assert nota_completa is not None
        assert nota_completa.nsu == "000000000000015"
        assert nota_completa.categoria_schema == "NFE_COMPLETA"
        assert nota_completa.tipo_documento == "NFE"
        assert nota_completa.manifestacao_necessaria is False
        assert nota_completa.situacao == "AUTORIZADA"
        assert nota_completa.numero == "1001"
        assert nota_completa.destinatario_documento == "22333444000181"
        assert nota_completa.destinatario_nome == "COMERCIO BETA DISTRIBUIDORA LTDA"
        assert nota_completa.valor_total == Decimal("1595.00")
        assert repo.obter_xml_nota_recebida(CHAVE_NFE_1001) == xml_completo_1001

        # 3. Se um resNFe atrasado ou repetido chegar para a mesma chave, NÃO faz downgrade
        status_3 = repo.salvar_documento_processado(doc_resumo, ambiente=2)
        assert status_3 == "IGNORADA"
        nota_apos_tentativa = repo.obter_nota_recebida(CHAVE_NFE_1001)
        assert nota_apos_tentativa is not None
        assert nota_apos_tentativa.categoria_schema == "NFE_COMPLETA"
        assert nota_apos_tentativa.manifestacao_necessaria is False
        assert repo.obter_xml_nota_recebida(CHAVE_NFE_1001) == xml_completo_1001

    def test_evento_cancelamento_110111_marca_nota_como_cancelada(self, tmp_path: Path) -> None:
        repo = RepositorioDistDFeSQLite(tmp_path / "teste_evento.db")

        xml_completo = (FIXTURES_DIR / "nfe_55_valida.xml").read_bytes()
        doc_nfe = processar_item_doc_zip(
            ItemDocZip(
                nsu="000000000000020",
                schema_xml="procNFe_v4.00.xsd",
                conteudo_base64=compactar_xml_para_doc_zip_b64(xml_completo),
            )
        )
        assert repo.salvar_documento_processado(doc_nfe, ambiente=2) == "NOVA"

        xml_evento = (FIXTURES_DIR / "nfe_evento_cancelamento.xml").read_bytes()
        doc_evento = processar_item_doc_zip(
            ItemDocZip(
                nsu="000000000000021",
                schema_xml="procEventoNFe_v1.00.xsd",
                conteudo_base64=compactar_xml_para_doc_zip_b64(xml_evento),
            )
        )
        assert repo.salvar_documento_processado(doc_evento, ambiente=2) == "EVENTO"

        nota = repo.obter_nota_recebida(CHAVE_NFE_1001)
        assert nota is not None
        assert nota.situacao == "CANCELADA"

    def test_listagem_e_filtros_por_emitente_periodo_valor_categoria_e_situacao(
        self, tmp_path: Path
    ) -> None:
        repo = RepositorioDistDFeSQLite(tmp_path / "teste_filtros.db")

        # Nota 1: procNFe completa (emissão 2026-03-10, valor 1595.00, emitente ALFA 11222333000181)
        xml_nfe = (FIXTURES_DIR / "nfe_55_valida.xml").read_bytes()
        repo.salvar_documento_processado(
            processar_item_doc_zip(
                ItemDocZip(
                    nsu="000000000000001",
                    schema_xml="procNFe_v4.00.xsd",
                    conteudo_base64=compactar_xml_para_doc_zip_b64(xml_nfe),
                )
            ),
            ambiente=2,
        )

        # Nota 2: resNFe resumo (emissão 2026-03-16, valor 2450.00, emitente customizado OMEGA)
        xml_resumo_omega = (
            (FIXTURES_DIR / "nfe_resumo.xml")
            .read_bytes()
            .replace(b"<CNPJ>11222333000181</CNPJ>", b"<CNPJ>22333444000181</CNPJ>")
            .replace(
                b"<xNome>INDUSTRIA ALFA METALURGICA LTDA</xNome>",
                b"<xNome>FORNECEDOR OMEGA QUIMICA SA</xNome>",
            )
        )
        repo.salvar_documento_processado(
            processar_item_doc_zip(
                ItemDocZip(
                    nsu="000000000000002",
                    schema_xml="resNFe_v1.01.xsd",
                    conteudo_base64=compactar_xml_para_doc_zip_b64(xml_resumo_omega),
                )
            ),
            ambiente=2,
        )

        todas = repo.listar_notas_recebidas(ambiente=2)
        assert len(todas) == 2
        assert isinstance(todas[0], RegistroNotaRecebida)
        # Ordenado por data_emissao DESC (2026-03-16 vem antes de 2026-03-10)
        assert [n.chave for n in todas] == [CHAVE_RESUMO_1007, CHAVE_NFE_1001]

        # Filtro por emitente (nome parcial ou CNPJ com/sem máscara)
        por_nome = repo.listar_notas_recebidas(emitente="OMEGA")
        assert [n.chave for n in por_nome] == [CHAVE_RESUMO_1007]

        por_cnpj = repo.listar_notas_recebidas(emitente="11.222.333/0001-81")
        assert [n.chave for n in por_cnpj] == [CHAVE_NFE_1001]

        # Filtro por período (data_inicio / data_fim)
        apenas_apos_12 = repo.listar_notas_recebidas(data_inicio=date(2026, 3, 12))
        assert [n.chave for n in apenas_apos_12] == [CHAVE_RESUMO_1007]

        apenas_ate_12 = repo.listar_notas_recebidas(data_fim="2026-03-12")
        assert [n.chave for n in apenas_ate_12] == [CHAVE_NFE_1001]

        # Filtro por faixa de valor
        acima_2000 = repo.listar_notas_recebidas(valor_min=Decimal("2000.00"))
        assert [n.chave for n in acima_2000] == [CHAVE_RESUMO_1007]

        ate_1800 = repo.listar_notas_recebidas(valor_max=Decimal("1800.00"))
        assert [n.chave for n in ate_1800] == [CHAVE_NFE_1001]

        # Filtro por categoria e situação
        apenas_resumo = repo.listar_notas_recebidas(categoria="RESUMO_NFE")
        assert [n.chave for n in apenas_resumo] == [CHAVE_RESUMO_1007]

        apenas_autorizada = repo.listar_notas_recebidas(situacao="AUTORIZADA")
        assert [n.chave for n in apenas_autorizada] == [CHAVE_NFE_1001]

    def test_sincronizar_dist_dfe_orquestra_loop_persiste_notas_eventos_e_sync_log(
        self, tmp_path: Path
    ) -> None:
        repo = RepositorioDistDFeSQLite(tmp_path / "teste_sync.db")
        agora = datetime(2026, 3, 19, 10, 0, tzinfo=UTC)

        xml_resumo = (FIXTURES_DIR / "nfe_resumo.xml").read_bytes()
        xml_nfe = (FIXTURES_DIR / "nfe_55_valida.xml").read_bytes()
        xml_evento = (FIXTURES_DIR / "nfe_evento_cancelamento.xml").read_bytes()

        paginas = [
            RespostaDistDFe(
                ambiente=2,
                ver_aplic="1.6.3",
                cstat=CSTAT_DOCS_LOCALIZADOS,
                xmotivo="Documento(s) localizado(s)",
                dh_resp=agora,
                ult_nsu="000000000000002",
                max_nsu="000000000000003",
                lote_doc_zip=[
                    ItemDocZip(
                        nsu="000000000000001",
                        schema_xml="resNFe_v1.01.xsd",
                        conteudo_base64=compactar_xml_para_doc_zip_b64(xml_resumo),
                    ),
                    ItemDocZip(
                        nsu="000000000000002",
                        schema_xml="procNFe_v4.00.xsd",
                        conteudo_base64=compactar_xml_para_doc_zip_b64(xml_nfe),
                    ),
                ],
            ),
            RespostaDistDFe(
                ambiente=2,
                ver_aplic="1.6.3",
                cstat=CSTAT_DOCS_LOCALIZADOS,
                xmotivo="Documento(s) localizado(s)",
                dh_resp=agora,
                ult_nsu="000000000000003",
                max_nsu="000000000000003",
                lote_doc_zip=[
                    ItemDocZip(
                        nsu="000000000000003",
                        schema_xml="procEventoNFe_v1.00.xsd",
                        conteudo_base64=compactar_xml_para_doc_zip_b64(xml_evento),
                    ),
                ],
            ),
        ]
        idx = 0

        def fake_consultar(ult_nsu: str) -> RespostaDistDFe:
            nonlocal idx
            resp = paginas[idx]
            idx += 1
            return resp

        resumo = sincronizar_dist_dfe(
            consultar_fn=fake_consultar,
            repo=repo,
            ambiente=2,
            relogio_fn=lambda: agora,
        )

        assert isinstance(resumo, ResumoExecucaoSync)
        assert resumo.paginas_consultadas == 2
        assert resumo.documentos_processados == 3
        assert resumo.novas_notas == 2
        assert resumo.notas_atualizadas == 0
        assert resumo.eventos_registrados == 1
        assert resumo.fila_zerada is True
        assert resumo.bloqueado_656 is False
        assert resumo.cursor.ultimo_nsu == "000000000000003"

        # Cursor salvo no SQLite
        assert repo.obter_cursor(ambiente=2).ultimo_nsu == "000000000000003"

        # Nota 1001 foi criada no NSU 2 e marcada como CANCELADA pelo evento do NSU 3
        nota_1001 = repo.obter_nota_recebida(CHAVE_NFE_1001)
        assert nota_1001 is not None
        assert nota_1001.situacao == "CANCELADA"

        # Log de sincronização registrado
        logs = repo.listar_logs_sync()
        assert len(logs) == 1
        assert isinstance(logs[0], RegistroSyncLog)
        assert logs[0].cstat == "138"
        assert logs[0].paginas == 2
        assert logs[0].novos_docs == 3
        assert logs[0].ultimo_nsu == "000000000000003"
