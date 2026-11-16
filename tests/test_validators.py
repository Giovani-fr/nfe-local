"""Testes unitários do motor de validadores (`validators/`), um teste por regra."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from models import DocumentoFiscal, Item, Parte, Totais
from parsers import ErroLayoutDesconhecido, ErroParseXML, parse_nfe
from validators import (
    analisar_documento,
    calcular_dv_chave_44,
    calcular_dv_cnpj,
    chave_44_valida,
    chave_nfse_50_valida,
    cnpj_valido,
    cpf_valido,
    criar_resultado_erro_xml,
    validar_chave_documento,
    validar_financeiro,
    validar_lote,
    validar_partes,
)

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestRegra1ChaveAcesso:
    """Regra 1: Chave de acesso 44 posições (numérica/alfanumérica) e 50 posições NFS-e."""

    def test_chave_44_numerica_e_alfanumerica_validas(self) -> None:
        chave_num = "35260311222333000181550010000010011100010010"
        chave_alnum = "35260312ABC34501DE35550010000010031100010032"
        assert chave_44_valida(chave_num) is True
        assert chave_44_valida(f"NFe{chave_num}") is True
        assert chave_44_valida(chave_alnum) is True
        assert calcular_dv_chave_44(chave_alnum[:43]) == 2

    def test_chave_44_dv_invalido_detectado_na_fixture(self) -> None:
        doc = parse_nfe((PASTA_FIXTURES / "nfe_55_chave_dv_invalido.xml").read_bytes())
        alertas = validar_chave_documento(doc)
        codigos = [a.codigo for a in alertas]
        assert "CHAVE_DV_INVALIDO" in codigos
        assert all(a.severidade == "ERRO" for a in alertas)

    def test_chave_tamanho_invalido_ou_cnpj_divergente(self) -> None:
        doc_curto = DocumentoFiscal(tipo="NFE", modelo="55", chave_acesso="12345")
        alertas_curto = validar_chave_documento(doc_curto)
        assert any(a.codigo == "CHAVE_ACESSO_INVALIDA" for a in alertas_curto)

        doc_div = parse_nfe((PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes())
        doc_div.emitente.documento = "22333444000181"
        alertas_div = validar_chave_documento(doc_div)
        assert any(a.codigo == "CHAVE_CNPJ_EMITENTE_DIVERGENTE" for a in alertas_div)

    def test_chave_nfse_50_posicoes(self) -> None:
        chave_50 = "35503082233444555000181000000000050126030000001005"
        assert chave_nfse_50_valida(chave_50) is True
        assert chave_nfse_50_valida("NFS" + chave_50) is True
        assert chave_nfse_50_valida("123") is False

        doc_nfse_inv = DocumentoFiscal(tipo="NFSE", modelo="NFSE", chave_acesso="12345")
        alertas = validar_chave_documento(doc_nfse_inv)
        assert any(a.codigo == "CHAVE_NFSE_INVALIDA" for a in alertas)


class TestRegra2DocumentosParte:
    """Regra 2: CPF (11 dígitos) e CNPJ (14 caracteres `[0-9A-Z]` NT 2014.002 v1.40)."""

    def test_cpf_valido_e_invalido(self) -> None:
        assert cpf_valido("123.456.789-09") is True
        assert cpf_valido("12345678900") is False
        assert cpf_valido("11111111111") is False
        assert cpf_valido("00000000000") is False
        assert cpf_valido("123") is False

    def test_cnpj_numerico_e_alfanumerico(self) -> None:
        assert cnpj_valido("11.222.333/0001-81") is True
        assert cnpj_valido("12.ABC.345/01DE-35") is True
        assert calcular_dv_cnpj("12ABC34501DE") == "35"
        assert cnpj_valido("12ABC34501DE99") is False
        assert cnpj_valido("00000000000000") is False
        assert cnpj_valido("11111111111111") is False

    def test_validar_partes_emitente_e_destinatario(self) -> None:
        doc = parse_nfe((PASTA_FIXTURES / "nfe_55_cnpj_alfanumerico.xml").read_bytes())
        assert validar_partes(doc) == []

        doc.emitente.documento = "12ABC34501DE00"
        doc.destinatario = Parte(documento="12345678900", tipo_pessoa="PF", razao_social="PF INV")
        alertas = validar_partes(doc)
        codigos = {a.codigo for a in alertas}
        assert "EMITENTE_DOCUMENTO_INVALIDO" in codigos
        assert "DESTINATARIO_DOCUMENTO_INVALIDO" in codigos


class TestRegra3BatimentoFinanceiro:
    """Regra 3: Soma dos itens == vProd e consistência da equação do vNF."""

    def test_nfe_valida_passa_sem_alertas_financeiros(self) -> None:
        doc = parse_nfe((PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes())
        assert validar_financeiro(doc) == []

    def test_nfe_soma_errada_gera_erros_soma_itens_e_total_nota(self) -> None:
        doc = parse_nfe((PASTA_FIXTURES / "nfe_55_soma_errada.xml").read_bytes())
        alertas = validar_financeiro(doc)
        codigos = {a.codigo for a in alertas}
        assert "SOMA_ITENS_DIVERGENTE" in codigos
        assert "TOTAL_NOTA_DIVERGENTE" in codigos
        assert all(a.severidade == "ERRO" for a in alertas)

    def test_tolerancia_centavo_opcional_e_calculo_unitario_item(self) -> None:
        doc = DocumentoFiscal(
            tipo="NFE",
            modelo="55",
            itens=[
                Item(
                    numero_item=1,
                    codigo="X1",
                    descricao="ITEM COM ARREDONDAMENTO",
                    quantidade=Decimal("3.0000"),
                    valor_unitario=Decimal("10.0000"),
                    valor_total=Decimal("30.20"),
                )
            ],
            totais=Totais(
                valor_produtos_servicos=Decimal("30.21"),
                valor_total_nota=Decimal("30.21"),
            ),
        )
        alertas_estrito = validar_financeiro(doc, tolerancia=Decimal("0.00"))
        assert any(a.codigo == "SOMA_ITENS_DIVERGENTE" for a in alertas_estrito)
        assert any(a.codigo == "ITEM_CALCULO_DIVERGENTE" for a in alertas_estrito)

        alertas_tolerante = validar_financeiro(doc, tolerancia=Decimal("0.01"))
        assert not any(a.codigo == "SOMA_ITENS_DIVERGENTE" for a in alertas_tolerante)


class TestRegras456LoteAmbienteProtocoloErros:
    """Regras 4, 5 e 6: Chave duplicada no lote, Homologação/Sem Protocolo e XML malformado."""

    def test_regra_5_alerta_homologacao_e_sem_protocolo(self) -> None:
        doc_homolog = parse_nfe((PASTA_FIXTURES / "nfe_55_homologacao.xml").read_bytes())
        res_homolog = analisar_documento("nfe_55_homologacao.xml", doc_homolog)
        assert res_homolog.valido is True
        assert any(
            a.codigo == "AMBIENTE_HOMOLOGACAO" and a.severidade == "AVISO"
            for a in res_homolog.alertas
        )

        doc_sem_prot = parse_nfe((PASTA_FIXTURES / "nfe_55_sem_protocolo.xml").read_bytes())
        res_sem_prot = analisar_documento("nfe_55_sem_protocolo.xml", doc_sem_prot)
        assert res_sem_prot.valido is True
        assert any(
            a.codigo == "SEM_PROTOCOLO" and a.severidade == "AVISO" for a in res_sem_prot.alertas
        )

    def test_regra_4_chave_duplicada_no_lote_ignora_evento_vinculado(self) -> None:
        doc_nfe = parse_nfe((PASTA_FIXTURES / "nfe_55_valida.xml").read_bytes())
        doc_ev = parse_nfe((PASTA_FIXTURES / "nfe_evento_cancelamento.xml").read_bytes())

        res_1 = analisar_documento("nota_1.xml", doc_nfe)
        res_ev = analisar_documento("evento_1.xml", doc_ev)

        lote_sem_duplicata = validar_lote([res_1, res_ev])
        assert lote_sem_duplicata[0].valido is True
        assert lote_sem_duplicata[1].valido is True

        res_copia = analisar_documento("nota_1_copia.xml", doc_nfe.model_copy(deep=True))
        lote_com_duplicata = validar_lote([res_1, res_copia, res_ev])
        assert lote_com_duplicata[0].valido is False
        assert lote_com_duplicata[1].valido is False
        assert any(a.codigo == "CHAVE_DUPLICADA_LOTE" for a in lote_com_duplicata[0].alertas)
        assert any(a.codigo == "CHAVE_DUPLICADA_LOTE" for a in lote_com_duplicata[1].alertas)
        assert lote_com_duplicata[2].valido is True

    def test_regra_6_xml_malformado_e_layout_desconhecido_nao_derrubam_lote(self) -> None:
        arquivos = (
            "nfe_55_valida.xml",
            "xml_malformado.xml",
            "nfse_abrasf_legada.xml",
            "nfce_65_valida.xml",
        )
        resultados = []
        for nome in arquivos:
            dados = (PASTA_FIXTURES / nome).read_bytes()
            try:
                doc = parse_nfe(dados)
                resultados.append(analisar_documento(nome, doc))
            except ErroLayoutDesconhecido as exc:
                resultados.append(
                    criar_resultado_erro_xml(nome, str(exc), codigo="LAYOUT_DESCONHECIDO")
                )
            except ErroParseXML as exc:
                resultados.append(criar_resultado_erro_xml(nome, str(exc), codigo="XML_MALFORMADO"))

        resultados = validar_lote(resultados)
        assert len(resultados) == 4
        assert resultados[0].valido is True
        assert resultados[1].valido is False
        assert resultados[1].erro_fatal is not None
        assert resultados[1].alertas[0].codigo == "XML_MALFORMADO"
        assert resultados[2].valido is False
        assert resultados[2].alertas[0].codigo == "LAYOUT_DESCONHECIDO"
        assert resultados[3].valido is True
