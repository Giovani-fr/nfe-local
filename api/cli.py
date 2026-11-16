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

_EXTENSOES_SUPORTADAS = {".xml", ".zip"}


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


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada principal da CLI."""
    parser = criar_parser()
    args = parser.parse_args(argv)

    if args.subcomando == "analisar":
        return _executar_analisar(args)
    if args.subcomando == "servidor":
        return _executar_servidor(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
