"""Pasta de dados/segredos e proteção da senha do certificado (DPAPI do Windows).

- `NFE_DATA_DIR`: pasta de dados e segredos. Padrão: `%LOCALAPPDATA%\\nfe-local` no Windows e
  `$XDG_DATA_HOME/nfe-local` (ou `~/.local/share/nfe-local`) nos demais sistemas. É sempre uma
  pasta **fora do projeto** e, por padrão, fora do OneDrive.
- A senha do `.pfx` nunca é gravada em texto puro: no Windows ela é cifrada com a DPAPI no escopo do
  usuário atual (só a mesma conta de usuário, na mesma máquina, consegue decifrar).
- Onde a DPAPI não existe (Linux/macOS/Docker), a senha **não é gravada**: use `NFE_CERT_SENHA`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

NOME_SUBPASTA_APP = "nfe-local"
NOME_SUBPASTA_CERTIFICADO = "certificado"
_ENTROPIA = b"nfe-local:certificado:senha:v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_MSG_SEM_DPAPI = "DPAPI indisponível neste sistema. Defina a senha em NFE_CERT_SENHA."

# Nomes de pastas de sincronização em nuvem mais comuns.
_RE_PASTA_SINCRONIZADA = re.compile(
    r"(^|[\\/])(onedrive[^\\/]*|dropbox[^\\/]*|google ?drive[^\\/]*|my drive"
    r"|icloud ?drive[^\\/]*)([\\/]|$)",
    re.IGNORECASE,
)


class ErroProtecaoSenha(Exception):
    """A senha não pôde ser protegida ou recuperada (DPAPI indisponível, outro usuário, etc.)."""


def diretorio_dados() -> Path:
    """Pasta de dados/segredos: `NFE_DATA_DIR` ou o padrão por usuário, fora do projeto."""
    env = os.environ.get("NFE_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        raiz = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "").strip()
        raiz = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return raiz / NOME_SUBPASTA_APP


def diretorio_certificado_padrao() -> Path:
    """Pasta padrão do certificado: `<diretorio_dados>/certificado`."""
    return diretorio_dados() / NOME_SUBPASTA_CERTIFICADO


def pasta_parece_sincronizada(caminho: str | Path) -> bool:
    """Heurística: o caminho está dentro de OneDrive, Dropbox, Google Drive ou iCloud Drive?"""
    texto = str(Path(caminho).expanduser().absolute())
    if _RE_PASTA_SINCRONIZADA.search(texto):
        return True
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        raiz = os.environ.get(var, "").strip()
        if raiz and texto.lower().startswith(raiz.lower().rstrip("\\/") + os.sep):
            return True
    return False


def dpapi_disponivel() -> bool:
    """`True` apenas no Windows (a DPAPI é parte do sistema, sem dependências extras)."""
    return sys.platform == "win32"


def _blob(dados: bytes):  # pragma: no cover - só Windows
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(dados, len(dados))
    blob = _DataBlob(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf, _DataBlob


def _chamar_dpapi(dados: bytes, *, proteger: bool) -> bytes:  # pragma: no cover - só Windows
    import ctypes

    entrada, _buf_in, DataBlob = _blob(dados)
    entropia, _buf_e, _ = _blob(_ENTROPIA)
    saida = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    funcao = crypt32.CryptProtectData if proteger else crypt32.CryptUnprotectData
    ok = funcao(
        ctypes.byref(entrada),
        None,
        ctypes.byref(entropia),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(saida),
    )
    if not ok:
        raise ErroProtecaoSenha(
            "Falha na DPAPI do Windows (o dado pode ter sido protegido por outro usuário "
            "ou outra máquina)."
        )
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def proteger_bytes(dados: bytes) -> bytes:
    """Cifra `dados` com a DPAPI (escopo do usuário atual)."""
    if not dpapi_disponivel():
        raise ErroProtecaoSenha(_MSG_SEM_DPAPI)
    return _chamar_dpapi(dados, proteger=True)  # pragma: no cover - só Windows


def desproteger_bytes(dados: bytes) -> bytes:
    """Decifra dados protegidos por `proteger_bytes` (mesmo usuário, mesma máquina)."""
    if not dpapi_disponivel():
        raise ErroProtecaoSenha(_MSG_SEM_DPAPI)
    return _chamar_dpapi(dados, proteger=False)  # pragma: no cover - só Windows


def proteger_senha(senha: str) -> bytes:
    return proteger_bytes(senha.encode("utf-8"))


def desproteger_senha(dados: bytes) -> str:
    return desproteger_bytes(dados).decode("utf-8")
