"""Interface de linha de comando (CLI) do nfe-local."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from api import __version__


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada principal da CLI."""
    parser = criar_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
