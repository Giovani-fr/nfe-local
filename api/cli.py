"""Interface de linha de comando (CLI) do nfe-local."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

from api import __version__
from models.fiscal import ResultadoAnalise
from parsers.detector import iterar_lote
from reports.exportacao import exportar_itens_csv, exportar_lote_xlsx, exportar_notas_csv
from reports.resumo import ResumoLote, gerar_resumo_lote
from sefaz.adn import ClienteADN, ErroBloqueioADN, resolver_tentativas
from sefaz.adn_demo import CNPJ_DEMO, montar_itens_demo
from sefaz.adn_diagnostico import DiagnosticoADN, diagnosticar_adn
from sefaz.adn_sync import (
    MAX_PAGINAS_PADRAO,
    ResumoSyncADN,
    carregar_demo_adn,
    sincronizar_adn,
)
from sefaz.cert_a1 import (
    ErroCertificadoA1,
    carregar_certificado_configurado,
    carregar_certificado_de_env,
)
from sefaz.controle_nsu import ErroBloqueioSEFAZ, EstadoCursorNSU
from sefaz.dist_dfe import (
    ClienteNFeDistribuicaoDFe,
    ErroComunicacaoSEFAZ,
    RespostaDistDFe,
    parse_resposta_dist_dfe,
    resolver_ambiente_sefaz,
)
from sefaz.storage import (
    RepositorioDistDFeSQLite,
    ResumoExecucaoSync,
    sincronizar_dist_dfe,
)

_EXTENSOES_SUPORTADAS = {".xml", ".zip"}
_PASTA_SOAP_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sefaz"


def criar_parser() -> argparse.ArgumentParser:
    """Cria e configura o parser de argumentos da CLI."""
    parser = argparse.ArgumentParser(
        prog="nfe-local",
        description=(
            "Visualizador, validador e analisador em lote de documentos fiscais "
            "eletrônicos brasileiros (NF-e, NFC-e e NFS-e Padrão Nacional)."
        ),
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Exibe a versão atual da ferramenta e encerra.",
    )

    subparsers = parser.add_subparsers(dest="subcomando", metavar="<subcomando>")

    parser_analisar = subparsers.add_parser(
        "analisar",
        help="Analisa, valida e exporta um ou mais arquivos .xml, .zip ou pastas.",
    )
    parser_analisar.add_argument(
        "caminhos",
        nargs="+",
        help="Caminhos de arquivos (.xml / .zip) ou diretórios a processar.",
    )
    parser_analisar.add_argument(
        "--json",
        dest="saida_json",
        action="store_true",
        help="Imprime na saída padrão o JSON estruturado (resumo e resultados).",
    )
    parser_analisar.add_argument(
        "--exportar-csv",
        dest="exportar_csv",
        metavar="ARQUIVO.csv",
        help="Salva o CSV consolidado de notas no caminho indicado.",
    )
    parser_analisar.add_argument(
        "--exportar-itens-csv",
        dest="exportar_itens_csv",
        metavar="ARQUIVO.csv",
        help="Salva o CSV detalhado de itens no caminho indicado.",
    )
    parser_analisar.add_argument(
        "--exportar-xlsx",
        dest="exportar_xlsx",
        metavar="ARQUIVO.xlsx",
        help="Salva a planilha XLSX multi-abas no caminho indicado.",
    )

    parser_servidor = subparsers.add_parser(
        "servidor",
        help="Inicia o servidor web local (FastAPI / Uvicorn).",
    )
    parser_servidor.add_argument(
        "--host",
        default="127.0.0.1",
        help="Endereço IP de escuta do servidor (padrão: 127.0.0.1).",
    )
    parser_servidor.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Porta TCP do servidor (padrão: 8765).",
    )

    parser_sync = subparsers.add_parser(
        "sincronizar-sefaz",
        help="Sincroniza notas emitidas contra o CNPJ via NFeDistribuicaoDFe no SQLite local.",
    )
    parser_sync.add_argument(
        "--db",
        dest="caminho_db",
        default=None,
        metavar="ARQUIVO.db",
        help="Caminho do banco SQLite local (padrão: data/nfe_recebidas.db).",
    )
    parser_sync.add_argument(
        "--ambiente",
        type=int,
        choices=(1, 2),
        default=None,
        help="Ambiente SEFAZ: 1=Produção, 2=Homologação (padrão: 2).",
    )
    parser_sync.add_argument(
        "--demo",
        action="store_true",
        help="Executa sincronização local usando envelopes SOAP simulados sem certificado real.",
    )
    parser_sync.add_argument(
        "--json",
        dest="saida_json",
        action="store_true",
        help="Imprime na saída padrão o JSON estruturado (ResumoExecucaoSync).",
    )

    parser_adn = subparsers.add_parser(
        "sincronizar-adn",
        help="Busca NFS-e (emitidas e tomadas) do CNPJ do certificado no ADN Nacional.",
    )
    parser_adn.add_argument(
        "--db",
        dest="caminho_db",
        default=None,
        metavar="ARQUIVO.db",
        help="Caminho do banco SQLite local (padrão: data/nfe_recebidas.db).",
    )
    parser_adn.add_argument(
        "--ambiente",
        type=int,
        choices=(1, 2),
        default=None,
        help="Ambiente: 1=Produção, 2=Produção restrita (padrão: o cadastrado, ou 2).",
    )
    parser_adn.add_argument(
        "--max-paginas",
        type=int,
        default=MAX_PAGINAS_PADRAO,
        help=f"Máximo de páginas por execução (padrão: {MAX_PAGINAS_PADRAO}).",
    )
    parser_adn.add_argument(
        "--url-base",
        dest="url_base",
        default=None,
        metavar="https://...",
        help="URL base do ADN (sobrepõe NFE_ADN_URL_BASE e o padrão do ambiente).",
    )
    parser_adn.add_argument(
        "--diagnostico",
        action="store_true",
        help="Faz UMA consulta de leitura a GET /DFe/0 e mostra o resultado, sem gravar nada.",
    )
    parser_adn.add_argument(
        "--tentativas",
        type=int,
        choices=(1, 2, 3, 4, 5),
        default=None,
        help=(
            "Tentativas por consulta quando a CONEXÃO falha (1 a 5; padrão 3, ou "
            "NFE_ADN_TENTATIVAS). Com --diagnostico o padrão é 1 e o máximo 3."
        ),
    )
    parser_adn.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SEGUNDOS",
        help="Tempo máximo de cada chamada ao ADN, em segundos (padrão 30).",
    )
    parser_adn.add_argument(
        "--demo",
        action="store_true",
        help="Carrega NFS-e sintéticas (emitidas, tomadas, uma cancelada) sem rede.",
    )
    parser_adn.add_argument(
        "--json",
        dest="saida_json",
        action="store_true",
        help="Imprime na saída padrão o JSON estruturado do resumo.",
    )

    parser_reset = subparsers.add_parser(
        "resetar-banco",
        help=(
            "Zera completamente o banco SQLite local "
            "(lotes importados, notas recebidas, cursores NSU e logs)."
        ),
    )
    parser_reset.add_argument(
        "--db",
        dest="caminho_db",
        default=None,
        metavar="ARQUIVO.db",
        help="Caminho do banco SQLite local (padrão: data/nfe_recebidas.db).",
    )
    parser_reset.add_argument(
        "--json",
        dest="saida_json",
        action="store_true",
        help="Imprime na saída padrão o resultado estruturado em JSON.",
    )
    parser_reset.add_argument(
        "--sim",
        dest="confirmar_sim",
        action="store_true",
        help="Pula a pergunta de confirmação e apaga imediatamente (use em scripts).",
    )

    return parser


def _coletar_arquivos(caminhos_str: Sequence[str]) -> list[Path]:
    """Expande arquivos e varre pastas recursivamente buscando `.xml` e `.zip`."""
    arquivos: list[Path] = []
    for item in caminhos_str:
        caminho = Path(item)
        if not caminho.exists():
            raise FileNotFoundError(f"Caminho não encontrado: '{item}'")
        if caminho.is_file():
            if caminho.suffix.lower() not in _EXTENSOES_SUPORTADAS:
                raise ValueError(f"Extensão não suportada em '{item}'. Use arquivos .xml ou .zip.")
            arquivos.append(caminho)
        elif caminho.is_dir():
            encontrados = sorted(
                p
                for p in caminho.rglob("*")
                if p.is_file() and p.suffix.lower() in _EXTENSOES_SUPORTADAS
            )
            arquivos.extend(encontrados)
    return arquivos


def _iterar_entradas_disco(arquivos: Sequence[Path]) -> Iterator[tuple[str, bytes]]:
    for arq in arquivos:
        yield arq.name, arq.read_bytes()


def _imprimir_resumo_terminal(resumo: ResumoLote, resultados: list[ResultadoAnalise]) -> None:
    c = resumo.contagem
    imp = resumo.impostos_totais
    print("=" * 64)
    print(f"nfe-local v{__version__} — Resumo do Lote Fiscal")
    print("=" * 64)
    print(f"Total de documentos processados : {c.total_arquivos}")
    print(f"  - Válidos                     : {c.validos}")
    print(f"  - Com erros                   : {c.com_erros}")
    print(f"  - Com avisos                  : {c.com_avisos}")
    print(f"  - Erros fatais de XML/Layout  : {c.erros_fatais_xml}")
    if c.por_tipo:
        tipos_str = ", ".join(f"{k}: {v}" for k, v in sorted(c.por_tipo.items()))
        print(f"  - Por tipo                    : {tipos_str}")
    print("-" * 64)
    print(f"Valor Produtos/Serviços (R$)    : {resumo.valor_produtos_servicos_total:.2f}")
    print(f"Valor Líquido Total (R$)        : {resumo.valor_liquido_total:.2f}")
    print(f"Valor Total das Notas (R$)      : {resumo.valor_total_notas:.2f}")
    print(
        f"Impostos (R$)                   : "
        f"ICMS={imp.icms:.2f} | IPI={imp.ipi:.2f} | PIS={imp.pis:.2f} | "
        f"COFINS={imp.cofins:.2f} | ISS={imp.iss:.2f} | IBS/CBS={imp.ibs_cbs:.2f}"
    )
    if c.com_erros > 0:
        print("-" * 64)
        print("Alertas de erro encontrados:")
        for res in resultados:
            if not res.valido:
                codigos = ", ".join(a.codigo for a in res.alertas) or (res.erro_fatal or "ERRO")
                print(f"  [ERRO] {res.arquivo_origem}: {codigos}")
    print("=" * 64)


def _executar_analisar(args: argparse.Namespace) -> int:
    try:
        arquivos = _coletar_arquivos(args.caminhos)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    if not arquivos:
        print(
            "Erro: Nenhum arquivo .xml ou .zip encontrado nos caminhos informados.",
            file=sys.stderr,
        )
        return 1

    resultados = list(iterar_lote(_iterar_entradas_disco(arquivos)))
    resumo = gerar_resumo_lote(resultados)

    if args.exportar_csv:
        destino_csv = Path(args.exportar_csv)
        destino_csv.parent.mkdir(parents=True, exist_ok=True)
        destino_csv.write_bytes(exportar_notas_csv(resultados))

    if args.exportar_itens_csv:
        destino_itens = Path(args.exportar_itens_csv)
        destino_itens.parent.mkdir(parents=True, exist_ok=True)
        destino_itens.write_bytes(exportar_itens_csv(resultados))

    if args.exportar_xlsx:
        destino_xlsx = Path(args.exportar_xlsx)
        destino_xlsx.parent.mkdir(parents=True, exist_ok=True)
        destino_xlsx.write_bytes(exportar_lote_xlsx(resultados, resumo=resumo))

    if args.saida_json:
        payload = {
            "resumo": resumo.model_dump(mode="json"),
            "resultados": [r.model_dump(mode="json") for r in resultados],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _imprimir_resumo_terminal(resumo, resultados)

    return 0


def _executar_servidor(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("api.app:app", host=args.host, port=args.port, reload=False)
    return 0


def _imprimir_resumo_sync_terminal(resumo: ResumoExecucaoSync) -> None:
    cur = resumo.cursor
    nome_amb = "Produção" if cur.ambiente == 1 else "Homologação"
    print("=" * 64)
    print(f"nfe-local v{__version__} — Sincronização SEFAZ (NFeDistribuicaoDFe)")
    print("=" * 64)
    print(f"Ambiente SEFAZ                  : {cur.ambiente} ({nome_amb})")
    print(f"Páginas consultadas             : {resumo.paginas_consultadas}")
    print(f"Documentos processados (docZip) : {resumo.documentos_processados}")
    print(f"  - Novas notas                 : {resumo.novas_notas}")
    print(f"  - Notas atualizadas           : {resumo.notas_atualizadas}")
    print(f"  - Eventos registrados         : {resumo.eventos_registrados}")
    print(f"Cursor NSU (ultNSU / maxNSU)    : {cur.ultimo_nsu} / {cur.max_nsu}")
    print(f"Último retorno SEFAZ            : cStat={cur.ultimo_cstat} ({cur.ultimo_motivo})")
    print(f"Bloqueio cStat 656 ativo        : {'SIM' if resumo.bloqueado_656 else 'NÃO'}")
    print("=" * 64)


def _executar_sincronizar_sefaz(args: argparse.Namespace) -> int:
    amb = resolver_ambiente_sefaz(args.ambiente)
    repo = RepositorioDistDFeSQLite(args.caminho_db)

    if args.demo:
        lote1_path = _PASTA_SOAP_FIXTURES / "soap_ret_138_lote1.xml"
        lote2_path = _PASTA_SOAP_FIXTURES / "soap_ret_138_lote2.xml"
        if not lote1_path.is_file() or not lote2_path.is_file():
            print(
                "Erro: Envelopes SOAP de demonstração não encontrados em 'tests/fixtures/sefaz/'.",
                file=sys.stderr,
            )
            return 1

        repo.salvar_cursor(EstadoCursorNSU(ambiente=amb))

        def _consultar_demo(ult_nsu: str) -> RespostaDistDFe:
            if ult_nsu == "000000000000000":
                return parse_resposta_dist_dfe(lote1_path.read_bytes())
            return parse_resposta_dist_dfe(lote2_path.read_bytes())

        resumo = sincronizar_dist_dfe(
            consultar_fn=_consultar_demo,
            repo=repo,
            ambiente=amb,
        )
    else:
        try:
            pfx_bytes, senha, _ = carregar_certificado_de_env()
            cliente = ClienteNFeDistribuicaoDFe(
                pfx_bytes=pfx_bytes,
                senha=senha,
                ambiente=amb,
            )
            resumo = sincronizar_dist_dfe(
                consultar_fn=lambda ult_nsu: cliente.consultar_dist_nsu(ult_nsu=ult_nsu),
                repo=repo,
                ambiente=amb,
            )
        except (ErroCertificadoA1, ErroBloqueioSEFAZ, ErroComunicacaoSEFAZ) as exc:
            print(f"Erro: {exc}", file=sys.stderr)
            return 1

    if args.saida_json:
        print(json.dumps(resumo.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        _imprimir_resumo_sync_terminal(resumo)
    return 0


def _imprimir_resumo_adn_terminal(resumo: ResumoSyncADN) -> None:
    nome_amb = "Produção" if resumo.cursor.ambiente == 1 else "Produção restrita"
    print("=" * 64)
    print(f"nfe-local v{__version__} — Sincronização NFS-e (ADN Nacional)")
    print("=" * 64)
    print(f"Ambiente                        : {resumo.cursor.ambiente} ({nome_amb})")
    print(f"Páginas consultadas             : {resumo.paginas_consultadas}")
    print(f"Documentos recebidos            : {resumo.documentos_recebidos}")
    print(f"  - Novas NFS-e                 : {resumo.novas_notas}")
    print(f"  - NFS-e atualizadas           : {resumo.notas_atualizadas}")
    print(f"  - Eventos registrados         : {resumo.eventos_registrados}")
    for tipo, qtd in sorted(resumo.ignorados_por_tipo.items()):
        print(f"  - Ignorados ({tipo})".ljust(34) + f": {qtd}")
    if resumo.com_erro_processamento:
        print(f"  - Com erro de leitura         : {resumo.com_erro_processamento}")
    for papel, qtd in sorted(resumo.papeis.items()):
        print(f"  - Papel {papel}".ljust(34) + f": {qtd}")
    print(f"Cursor NSU (ADN)                : {resumo.cursor.ultimo_nsu}")
    print(f"Tentativas de conexão usadas    : {resumo.tentativas_usadas}")
    for falha in resumo.falhas_conexao:
        print(f"  - Falha de conexão            : {falha}")
    print(f"Encerrado por                   : {resumo.encerrado_por}")
    if resumo.mensagem:
        print(f"Mensagem                        : {resumo.mensagem}")
    print("=" * 64)


def _imprimir_diagnostico_adn(diag: DiagnosticoADN) -> None:
    def _data(d) -> str:
        return d.date().isoformat() if d else "-"

    print("=" * 64)
    print(f"nfe-local v{__version__} — Diagnóstico do ADN (somente leitura)")
    print("=" * 64)
    print(f"Ambiente                        : {diag.ambiente}")
    print(f"URL base                        : {diag.url_base}")
    print(f"CNPJ de consulta                : {diag.cnpj_consulta_mascarado}")
    print(
        f"Certificado válido              : {_data(diag.certificado_valido_de)} a "
        f"{_data(diag.certificado_valido_ate)}"
        f"{' (VENCIDO)' if diag.certificado_vencido else ''}"
    )
    print(
        f"Tentativas                      : {diag.tentativas_realizadas} de "
        f"{diag.tentativas_solicitadas} ({diag.tentativas_com_falha} com falha)"
        f" | timeout {diag.timeout_segundos:g}s"
    )
    print(f"Conexão TLS                     : {diag.conexao_tls}")
    if diag.tls_versao or diag.tls_cifrador:
        versao, cifrador = diag.tls_versao or "-", diag.tls_cifrador or "-"
        print(f"TLS negociado                   : {versao} / {cifrador}")
    falha = diag.falha_conexao or (diag.falhas_conexao[-1] if diag.falhas_conexao else None)
    if falha is not None:
        print(f"Falha - etapa                   : {falha.etapa}")
        print(f"Falha - exceção raiz            : {falha.excecao_raiz}")
        print(f"Falha - cadeia                  : {' > '.join(falha.cadeia_excecoes)}")
        print(f"Falha - código do SO            : {falha.codigo_so or '-'}")
        if falha.motivo_tls:
            print(f"Falha - motivo TLS              : {falha.motivo_tls}")
        encerrou = "SIM" if falha.servidor_encerrou_conexao else "NÃO"
        print(
            f"Falha - servidor encerrou       : {encerrou}"
            f" | tempo esgotado: {'SIM' if falha.tempo_esgotado else 'NÃO'}"
        )
        print(f"O que fazer                     : {falha.orientacao}")
    print(f"HTTP status                     : {diag.http_status if diag.http_status else '-'}")
    print(f"StatusProcessamento             : {diag.status_processamento or '-'}")
    print(f"TipoConsulta                    : {diag.tipo_consulta or '-'}")
    for rotulo, lista in (("Erros", diag.erros), ("Alertas", diag.alertas)):
        if lista:
            for m in lista:
                print(f"{rotulo}".ljust(32) + f": {m.codigo} - {m.descricao}")
        else:
            print(f"{rotulo}".ljust(32) + ": (nenhum)")
    if diag.itens_por_tipo_documento:
        for tipo, qtd in sorted(diag.itens_por_tipo_documento.items()):
            print(f"Itens {tipo}".ljust(32) + f": {qtd}")
    else:
        print("Itens no lote                   : 0")
    print(f"Resultado                       : {diag.interpretacao}")
    print("=" * 64)


def _executar_diagnostico_adn(args: argparse.Namespace) -> int:
    """Uma consulta somente leitura. Não abre nem cria o banco e não toca no cursor."""
    try:
        pfx_bytes, senha, _, config = carregar_certificado_configurado()
        amb = resolver_ambiente_sefaz(
            args.ambiente if args.ambiente is not None else config.ambiente
        )
        cliente = ClienteADN(
            pfx_bytes=pfx_bytes,
            senha=senha,
            ambiente=amb,
            base_url=args.url_base,
            timeout_segundos=args.timeout,
        )
    except (ErroCertificadoA1, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    diag = diagnosticar_adn(cliente, tentativas=args.tentativas or 1)
    if args.saida_json:
        print(json.dumps(diag.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        _imprimir_diagnostico_adn(diag)
    return 0 if diag.sucesso else 1


def _executar_sincronizar_adn(args: argparse.Namespace) -> int:
    if args.diagnostico:
        return _executar_diagnostico_adn(args)
    try:
        tentativas = resolver_tentativas(args.tentativas)
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    repo = RepositorioDistDFeSQLite(args.caminho_db)

    if args.demo:
        amb = resolver_ambiente_sefaz(args.ambiente)
        resumo = carregar_demo_adn(
            repo, montar_itens_demo(), documento_alvo=CNPJ_DEMO, ambiente=amb
        )
    else:
        try:
            pfx_bytes, senha, _, config = carregar_certificado_configurado()
            amb = resolver_ambiente_sefaz(
                args.ambiente if args.ambiente is not None else config.ambiente
            )
            cliente = ClienteADN(
                pfx_bytes=pfx_bytes,
                senha=senha,
                ambiente=amb,
                base_url=args.url_base,
                timeout_segundos=args.timeout,
            )
            resumo = sincronizar_adn(
                cliente.consultar_nsu,
                repo,
                documento_alvo=cliente.cnpj_consulta,
                ambiente=amb,
                max_paginas=args.max_paginas,
                tentativas=tentativas,
            )
        except (ErroCertificadoA1, ErroBloqueioADN, ValueError) as exc:
            print(f"Erro: {exc}", file=sys.stderr)
            return 1

    if args.saida_json:
        print(json.dumps(resumo.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        _imprimir_resumo_adn_terminal(resumo)
    return 0 if resumo.encerrado_por in ("FILA_ZERADA", "LIMITE_PAGINAS") else 1


def _confirmar_reset(caminho_db: Path) -> bool:
    """Pede confirmação explícita (a pergunta vai para o stderr, mantendo o stdout limpo)."""
    sys.stderr.write(
        "ATENÇÃO: isto apaga TODOS os lotes, notas recebidas, cursores NSU e logs de\n"
        f"{caminho_db}\n"
        "Digite APAGAR para confirmar (ou y/N): "
    )
    sys.stderr.flush()
    try:
        resposta = input().strip()
    except EOFError:
        return False
    return resposta == "APAGAR" or resposta.lower() in {"y", "yes", "s", "sim"}


def _executar_resetar_banco(args: argparse.Namespace) -> int:
    repo = RepositorioDistDFeSQLite(args.caminho_db)
    if not args.confirmar_sim and not _confirmar_reset(repo.caminho_db):
        print("Cancelado: nada foi apagado.", file=sys.stderr)
        return 1
    resultado = repo.resetar_banco_completo()
    if args.saida_json:
        payload = {
            "status": "ok",
            "caminho_db": str(repo.caminho_db),
            "lotes_removidos": resultado["lotes_removidos"],
            "recebidas_removidas": resultado["recebidas_removidas"],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=" * 64)
        print(f"nfe-local v{__version__} — Reset Completo do Banco SQLite Local")
        print("=" * 64)
        print(f"Banco SQLite        : {repo.caminho_db}")
        print(f"Lotes removidos     : {resultado['lotes_removidos']}")
        print(f"Recebidas removidas : {resultado['recebidas_removidas']}")
        print("Status              : Banco zerado e compactado (VACUUM).")
        print("=" * 64)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada principal da CLI."""
    parser = criar_parser()
    args = parser.parse_args(argv)

    if args.subcomando == "analisar":
        return _executar_analisar(args)
    if args.subcomando == "servidor":
        return _executar_servidor(args)
    if args.subcomando == "sincronizar-sefaz":
        return _executar_sincronizar_sefaz(args)
    if args.subcomando == "sincronizar-adn":
        return _executar_sincronizar_adn(args)
    if args.subcomando == "resetar-banco":
        return _executar_resetar_banco(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
