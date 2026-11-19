"""Testes unitários e de integração (100% offline) do Assistente LLM de Lote."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import (
    app,
    definir_servico_assistente,
    obter_repositorio_lotes,
    obter_servico_assistente,
)
from parsers.detector import processar_arquivo_xml
from reports.assistente_lote import (
    CachePerguntasLLM,
    ErroComunicacaoLLM,
    ErroLimiteUsoLLM,
    ErroLLMNaoConfigurado,
    LimitadorUsoLLM,
    ServicoAssistenteLote,
    montar_contexto_lote_llm,
)
from reports.resumo import gerar_resumo_lote
from validators.lote import validar_lote

PASTA_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _carregar_lote_teste():
    nomes = (
        "nfe_55_valida.xml",
        "nfe_55_soma_errada.xml",
        "nfe_55_homologacao.xml",
        "nfse_nacional_valida.xml",
    )
    resultados_brutos = [
        processar_arquivo_xml((PASTA_FIXTURES / nome).read_bytes(), nome) for nome in nomes
    ]
    resultados = validar_lote(resultados_brutos)
    resumo = gerar_resumo_lote(resultados)
    return resumo, resultados


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Cliente FastAPI isolado sem chave LLM configurada por padrão."""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_ENVIAR_IDENTIFICADORES", raising=False)
    monkeypatch.delenv("LLM_ENVIAR_AMOSTRA", raising=False)
    obter_repositorio_lotes().limpar()
    definir_servico_assistente(ServicoAssistenteLote())
    return TestClient(app)


class TestContextoCompactoLLM:
    """Testes da montagem determinística e enxuta do contexto do lote para o LLM."""

    def test_montar_contexto_inclui_totais_e_alertas_sem_vazar_xml_bruto(self) -> None:
        resumo, resultados = _carregar_lote_teste()
        contexto = montar_contexto_lote_llm(
            resumo,
            resultados,
            max_notas=3,
            enviar_identificadores=True,
            enviar_amostra=True,
        )

        assert "resumo" in contexto
        assert "notas_amostradas" in contexto
        assert contexto["total_notas_no_lote"] == 4
        assert len(contexto["notas_amostradas"]) == 3

        # Totais e contagens presentes no resumo
        assert contexto["resumo"]["contagem"]["total_arquivos"] == 4
        assert contexto["resumo"]["contagem"]["com_erros"] >= 1
        assert "impostos_totais" in contexto["resumo"]
        assert "por_emitente" in contexto["resumo"]
        assert "por_mes" in contexto["resumo"]

        # Prioriza notas com erros/alertas na amostragem
        arquivos_amostrados = [n["arquivo_origem"] for n in contexto["notas_amostradas"]]
        assert "nfe_55_soma_errada.xml" in arquivos_amostrados
        assert "nfe_55_homologacao.xml" in arquivos_amostrados

        nota_erro = next(
            n
            for n in contexto["notas_amostradas"]
            if n["arquivo_origem"] == "nfe_55_soma_errada.xml"
        )
        assert nota_erro["valido"] is False
        assert any(a["codigo"] == "SOMA_ITENS_DIVERGENTE" for a in nota_erro["alertas"])

        # Garante que nenhuma tag de XML bruto vazou no JSON enviado ao LLM
        serializado = json.dumps(contexto, ensure_ascii=False)
        assert "<nfeProc" not in serializado
        assert "<infNFe" not in serializado
        assert "<det " not in serializado
        assert "xmlns=" not in serializado


class TestCacheELimitadorLLM:
    """Testes do cache SHA-256 e do limitador de chamadas por hora."""

    def test_cache_sha256_normaliza_pergunta_e_nao_desconta_franquia(self) -> None:
        resumo, resultados = _carregar_lote_teste()
        chamadas_provedor: list[list[dict[str, str]]] = []

        def fake_completion(
            mensagens: list[dict[str, str]],
            modelo: str,
            base_url: str,
            api_key: str,
        ) -> str:
            chamadas_provedor.append(mensagens)
            return "A nota nfe_55_soma_errada.xml possui divergência na soma dos itens."

        servico = ServicoAssistenteLote(
            modelo="gpt-4o-mini",
            max_chamadas_hora=5,
            cliente_completion=fake_completion,
        )

        # 1ª chamada: aciona o provedor e consome 1 chamada da franquia
        resp1 = servico.perguntar(
            resumo=resumo,
            resultados=resultados,
            pergunta="Quais notas possuem erros ou alertas?",
        )
        assert resp1.em_cache is False
        assert len(chamadas_provedor) == 1
        assert resp1.chamadas_restantes == 4
        assert "nfe_55_soma_errada.xml" in resp1.resposta

        # 2ª chamada com variação de maiúsculas e espaços: retorna do cache sem chamar o provedor
        resp2 = servico.perguntar(
            resumo=resumo,
            resultados=resultados,
            pergunta="   QUAIS   notas possuem   erros ou alertas?  ",
        )
        assert resp2.em_cache is True
        assert resp2.resposta == resp1.resposta
        assert resp2.chave_cache == resp1.chave_cache
        assert len(chamadas_provedor) == 1
        assert resp2.chamadas_restantes == 4

    def test_cache_em_disco_persiste_entre_instancias(self, tmp_path: Path) -> None:
        arquivo_cache = tmp_path / "cache_llm.json"
        cache1 = CachePerguntasLLM(caminho_arquivo=arquivo_cache)
        chave = cache1.calcular_chave_cache({"total": "100.00"}, "Qual o valor total?")
        cache1.salvar(chave, "O valor total é R$ 100,00.")

        cache2 = CachePerguntasLLM(caminho_arquivo=arquivo_cache)
        assert cache2.obter(chave) == "O valor total é R$ 100,00."

    def test_limitador_uso_bloqueia_apos_max_chamadas_hora(self) -> None:
        relogio_atual = [1000.0]
        limitador = LimitadorUsoLLM(
            max_chamadas_hora=2,
            janela_segundos=3600.0,
            relogio=lambda: relogio_atual[0],
        )
        assert limitador.chamadas_restantes() == 2

        limitador.registrar_chamada()
        assert limitador.chamadas_restantes() == 1

        limitador.registrar_chamada()
        assert limitador.chamadas_restantes() == 0

        with pytest.raises(ErroLimiteUsoLLM) as exc_info:
            limitador.verificar_disponibilidade()
        assert "2" in str(exc_info.value)

        # Avança o relógio além de 1 hora (3601s): a janela deslizante libera novamente
        relogio_atual[0] += 3601.0
        assert limitador.chamadas_restantes() == 2
        limitador.registrar_chamada()
        assert limitador.chamadas_restantes() == 1

    def test_servico_sem_api_key_levanta_erro_nao_configurado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        resumo, resultados = _carregar_lote_teste()
        servico = ServicoAssistenteLote()

        status = servico.obter_status()
        assert status["configurado"] is False

        with pytest.raises(ErroLLMNaoConfigurado) as exc_info:
            servico.perguntar(
                resumo=resumo,
                resultados=resultados,
                pergunta="Qual o resumo de impostos?",
            )
        assert "LLM_API_KEY" in str(exc_info.value)


class TestEndpointsAPIAssistenteLLM:
    """Testes de integração das rotas `/api/llm/status` e `/api/lote/{lote_id}/perguntar`."""

    def test_estado_nao_configurado_retorna_503_e_status_false(self, client: TestClient) -> None:
        resp_status = client.get("/api/llm/status")
        assert resp_status.status_code == 200
        dados_status = resp_status.json()
        assert dados_status["configurado"] is False
        assert dados_status["modelo"] == "gpt-4o-mini"
        assert dados_status["max_chamadas_hora"] == 20

        lote_id = client.post("/api/lote/demo").json()["lote_id"]
        resp_perg = client.post(
            f"/api/lote/{lote_id}/perguntar",
            json={"pergunta": "Quais notas possuem erros ou alertas?"},
        )
        assert resp_perg.status_code == 503
        assert "LLM_API_KEY" in resp_perg.json()["detail"]

    def test_perguntar_com_cache_e_rate_limit_429(self, client: TestClient) -> None:
        contador_chamadas = 0

        def fake_completion(
            mensagens: list[dict[str, str]],
            modelo: str,
            base_url: str,
            api_key: str,
        ) -> str:
            nonlocal contador_chamadas
            contador_chamadas += 1
            return f"Resposta sintética #{contador_chamadas} baseada nos totais do lote."

        servico = ServicoAssistenteLote(
            modelo="modelo-teste-local",
            max_chamadas_hora=2,
            cliente_completion=fake_completion,
        )
        definir_servico_assistente(servico)
        assert obter_servico_assistente() is servico

        resp_status = client.get("/api/llm/status")
        assert resp_status.status_code == 200
        assert resp_status.json() == {
            "configurado": True,
            "modelo": "modelo-teste-local",
            "chamadas_restantes": 2,
            "max_chamadas_hora": 2,
            "enviar_identificadores": False,
            "enviar_amostra": False,
        }

        lote_id = client.post("/api/lote/demo").json()["lote_id"]

        # 1ª pergunta inédita -> chama provedor (restam 1)
        r1 = client.post(
            f"/api/lote/{lote_id}/perguntar",
            json={"pergunta": "Quais notas possuem erros ou alertas?"},
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["lote_id"] == lote_id
        assert d1["em_cache"] is False
        assert d1["modelo"] == "modelo-teste-local"
        assert d1["chamadas_restantes"] == 1
        assert contador_chamadas == 1

        # Mesma pergunta (com espaços/maiúsculas diferentes) -> vem do cache (restam 1)
        r1_cache = client.post(
            f"/api/lote/{lote_id}/perguntar",
            json={"pergunta": "  quais NOTAS possuem erros ou alertas? "},
        )
        assert r1_cache.status_code == 200
        d1_cache = r1_cache.json()
        assert d1_cache["em_cache"] is True
        assert d1_cache["resposta"] == d1["resposta"]
        assert d1_cache["chamadas_restantes"] == 1
        assert contador_chamadas == 1

        # 2ª pergunta inédita -> chama provedor (restam 0)
        r2 = client.post(
            f"/api/lote/{lote_id}/perguntar",
            json={"pergunta": "Qual o resumo de impostos por emitente?"},
        )
        assert r2.status_code == 200
        assert r2.json()["em_cache"] is False
        assert r2.json()["chamadas_restantes"] == 0
        assert contador_chamadas == 2

        # 3ª pergunta inédita -> atinge rate limit (HTTP 429)
        r3 = client.post(
            f"/api/lote/{lote_id}/perguntar",
            json={"pergunta": "Existem notas em homologação ou sem protocolo?"},
        )
        assert r3.status_code == 429
        assert "limite" in r3.json()["detail"].lower()
        assert contador_chamadas == 2

        # Pergunta já em cache continua funcionando normalmente (200 OK) mesmo com quota zerada!
        r_cache_apos_limite = client.post(
            f"/api/lote/{lote_id}/perguntar",
            json={"pergunta": "Quais notas possuem erros ou alertas?"},
        )
        assert r_cache_apos_limite.status_code == 200
        assert r_cache_apos_limite.json()["em_cache"] is True
        assert r_cache_apos_limite.json()["resposta"] == d1["resposta"]
        assert contador_chamadas == 2

    def test_falha_comunicacao_externa_retorna_502(self, client: TestClient) -> None:
        def completion_com_falha(
            mensagens: list[dict[str, str]],
            modelo: str,
            base_url: str,
            api_key: str,
        ) -> str:
            raise ErroComunicacaoLLM("Timeout ao contatar o provedor LLM externo.")

        definir_servico_assistente(ServicoAssistenteLote(cliente_completion=completion_com_falha))
        lote_id = client.post("/api/lote/demo").json()["lote_id"]

        resp = client.post(
            f"/api/lote/{lote_id}/perguntar",
            json={"pergunta": "Qual o valor líquido do lote?"},
        )
        assert resp.status_code == 502
        assert "Timeout" in resp.json()["detail"]

    def test_validacao_tamanho_pergunta_e_lote_inexistente(self, client: TestClient) -> None:
        lote_id = client.post("/api/lote/demo").json()["lote_id"]

        # Pergunta muito curta (< 3 chars) -> 422
        resp_curta = client.post(f"/api/lote/{lote_id}/perguntar", json={"pergunta": "oi"})
        assert resp_curta.status_code == 422

        # Lote inexistente -> 404
        resp_404 = client.post(
            "/api/lote/lote_inexistente/perguntar",
            json={"pergunta": "Quais notas possuem erros?"},
        )
        assert resp_404.status_code == 404
