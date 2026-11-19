"""Leitor seguro em memória de Certificado Digital ICP-Brasil A1 (`.pfx` / PKCS#12)."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtensionOID, NameOID
from pydantic import BaseModel, ConfigDict

from models.normalizadores import limpar_documento
from sefaz import segredos

OID_ICP_BRASIL_CNPJ = "2.16.76.1.3.3"
OID_ICP_BRASIL_CPF_RESPONSAVEL = "2.16.76.1.3.1"

# Formato antigo (inseguro): pasta dentro do projeto, com a senha em texto puro (`senha.txt`).
DIRETORIO_CERTIFICADO_LEGADO = Path(__file__).resolve().parent.parent / "data" / "certificado"
NOME_ARQUIVO_PFX_FIXO = "certificado.pfx"
NOME_ARQUIVO_SENHA_FIXO = "senha.txt"  # legado: texto puro, nunca mais gravado
NOME_ARQUIVO_SENHA_DPAPI = "senha.dpapi"  # senha cifrada pela DPAPI (usuario atual)
NOME_ARQUIVO_CONFIG_FIXO = "config.json"

_RE_CNPJ_FORMATADO_OU_PURO = re.compile(
    r"(?<![0-9A-Z])([0-9A-Z]{2}\.?[0-9A-Z]{3}\.?[0-9A-Z]{3}/?[0-9A-Z]{4}-?\d{2})(?![0-9A-Z])",
    re.IGNORECASE,
)


class ErroCertificadoA1(Exception):
    """Exceção levantada quando o certificado `.pfx` não pode ser lido ou é inválido."""


class MetadadosCertificado(BaseModel):
    """Metadados extraídos em memória de um Certificado Digital ICP-Brasil A1."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    titular: str
    documento: str = ""
    emissor: str
    valido_de: datetime
    valido_ate: datetime
    dias_para_vencer: int
    vencido: bool
    ainda_nao_valido: bool
    thumbprint_sha1: str


class ConfigCertificadoSEFAZ(BaseModel):
    """Configuração de UF autora, ambiente e origem do Certificado Digital A1."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    uf_autor: str = "35"
    ambiente: Literal[1, 2] = 2
    origem: Literal["interface", "env", "nao_configurado"] = "nao_configurado"


def carregar_pkcs12_memoria(
    pfx_bytes: bytes,
    senha: str | bytes | None,
) -> tuple[Any, x509.Certificate, list[x509.Certificate]]:
    """Carrega chave privada, certificado e cadeia PKCS#12 exclusivamente em memória."""
    if not pfx_bytes:
        raise ErroCertificadoA1("O conteúdo do arquivo de certificado (.pfx) está vazio.")

    if isinstance(senha, str):
        senha_bytes: bytes | None = senha.encode("utf-8") if senha != "" else b""
    else:
        senha_bytes = senha

    try:
        chave_privada, certificado, cadeia = pkcs12.load_key_and_certificates(
            pfx_bytes,
            senha_bytes,
        )
    except (ValueError, TypeError) as exc:
        raise ErroCertificadoA1(
            f"Falha ao abrir o certificado PKCS#12 (.pfx). Verifique a senha e a integridade: {exc}"
        ) from exc

    if chave_privada is None or certificado is None:
        raise ErroCertificadoA1(
            "O arquivo PKCS#12 (.pfx) não contém chave privada e certificado X.509 completos."
        )

    return chave_privada, certificado, list(cadeia or [])


def _decodificar_asn1_texto(der_bytes: bytes) -> str:
    """Extrai o texto de um elemento ASN.1 simples (UTF8String, PrintableString, IA5, OCTET)."""
    if len(der_bytes) >= 2 and der_bytes[0] in (0x04, 0x0C, 0x13, 0x16, 0x1E):
        tamanho = der_bytes[1]
        if tamanho < 0x80 and len(der_bytes) >= 2 + tamanho:
            return der_bytes[2 : 2 + tamanho].decode("utf-8", errors="ignore")
    return der_bytes.decode("utf-8", errors="ignore")


def _extrair_cnpj_oid_icp_brasil(certificado: x509.Certificate) -> str:
    """Busca o CNPJ de 14 caracteres na extensão SubjectAlternativeName (OID `2.16.76.1.3.3`)."""
    try:
        ext = certificado.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    except x509.ExtensionNotFound:
        return ""

    san = ext.value
    for other in san.get_values_for_type(x509.OtherName):
        if other.type_id.dotted_string == OID_ICP_BRASIL_CNPJ:
            texto_bruto = _decodificar_asn1_texto(other.value).strip()
            limpo = limpar_documento(texto_bruto)
            if len(limpo) == 14:
                return limpo
            match = _RE_CNPJ_FORMATADO_OU_PURO.search(texto_bruto)
            if match:
                candidato = limpar_documento(match.group(1))
                if len(candidato) == 14:
                    return candidato
    return ""


def _extrair_atributo_nome(nome: x509.Name, oid: x509.ObjectIdentifier) -> str:
    atributos = nome.get_attributes_for_oid(oid)
    if not atributos:
        return ""
    valor = atributos[0].value
    return valor.decode("utf-8", errors="ignore") if isinstance(valor, bytes) else str(valor)


def _extrair_cnpj_fallback_cn(common_name: str) -> str:
    """Extrai CNPJ de 14 posições do Common Name (ex.: `RAZAO SOCIAL:11222333000181`)."""
    if not common_name:
        return ""
    if ":" in common_name:
        sufixo = limpar_documento(common_name.rsplit(":", 1)[-1])
        if len(sufixo) == 14:
            return sufixo

    match = _RE_CNPJ_FORMATADO_OU_PURO.search(common_name)
    if match:
        candidato = limpar_documento(match.group(1))
        if len(candidato) == 14:
            return candidato
    return ""


def extrair_metadados_certificado(
    pfx_bytes: bytes,
    senha: str,
    *,
    agora_utc: datetime | None = None,
) -> MetadadosCertificado:
    """Lê o `.pfx` em memória e retorna `MetadadosCertificado` com titular, CNPJ e validade."""
    _, cert, _ = carregar_pkcs12_memoria(pfx_bytes, senha)

    cn_titular = _extrair_atributo_nome(cert.subject, NameOID.COMMON_NAME)
    if not cn_titular:
        cn_titular = cert.subject.rfc4514_string()

    cn_emissor = _extrair_atributo_nome(cert.issuer, NameOID.COMMON_NAME)
    if not cn_emissor:
        cn_emissor = cert.issuer.rfc4514_string()

    documento = _extrair_cnpj_oid_icp_brasil(cert)
    if not documento:
        documento = _extrair_cnpj_fallback_cn(cn_titular)

    valido_de = cert.not_valid_before_utc
    valido_ate = cert.not_valid_after_utc
    referencia = agora_utc if agora_utc is not None else datetime.now(UTC)

    delta = valido_ate - referencia
    dias_para_vencer = int(delta.total_seconds() // 86400)
    vencido = referencia > valido_ate
    ainda_nao_valido = referencia < valido_de
    thumbprint_sha1 = cert.fingerprint(hashes.SHA1()).hex().upper()  # noqa: S303

    return MetadadosCertificado(
        titular=cn_titular,
        documento=documento,
        emissor=cn_emissor,
        valido_de=valido_de,
        valido_ate=valido_ate,
        dias_para_vencer=dias_para_vencer,
        vencido=vencido,
        ainda_nao_valido=ainda_nao_valido,
        thumbprint_sha1=thumbprint_sha1,
    )


def carregar_certificado_de_env() -> tuple[bytes, str, MetadadosCertificado]:
    """Carrega `(pfx_bytes, senha, MetadadosCertificado)` de `NFE_CERT_PATH` e `NFE_CERT_SENHA`."""
    caminho_str = os.environ.get("NFE_CERT_PATH", "").strip()
    senha = os.environ.get("NFE_CERT_SENHA", "")

    if not caminho_str:
        raise ErroCertificadoA1(
            "Variável de ambiente 'NFE_CERT_PATH' não configurada. "
            "Informe o caminho do certificado digital A1 (.pfx) no arquivo .env."
        )

    caminho = Path(caminho_str).expanduser()
    if not caminho.is_file():
        raise ErroCertificadoA1(f"Arquivo de certificado digital A1 não encontrado em '{caminho}'.")

    try:
        pfx_bytes = caminho.read_bytes()
    except OSError as exc:
        raise ErroCertificadoA1(f"Erro ao ler o arquivo de certificado '{caminho}': {exc}") from exc

    metadados = extrair_metadados_certificado(pfx_bytes, senha)
    return pfx_bytes, senha, metadados


def resolver_diretorio_certificado(cert_dir: str | Path | None = None) -> Path:
    """Resolve a pasta do certificado: argumento, `NFE_CERT_DIR` ou `<NFE_DATA_DIR>/certificado`.

    O padrão fica fora do projeto (`%LOCALAPPDATA%\\nfe-local\\certificado` no Windows).
    """
    if cert_dir is not None:
        return Path(cert_dir)
    env_dir = os.environ.get("NFE_CERT_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    return segredos.diretorio_certificado_padrao()


def _senha_env() -> str | None:
    valor = os.environ.get("NFE_CERT_SENHA")
    return valor if valor else None


def _ler_senha_salva(pasta: Path) -> str | None:
    """Senha do cadastro local: `senha.dpapi` (decifrada) ou, na falta dele, `NFE_CERT_SENHA`."""
    arq = pasta / NOME_ARQUIVO_SENHA_DPAPI
    if arq.is_file():
        try:
            return segredos.desproteger_senha(arq.read_bytes())
        except (OSError, segredos.ErroProtecaoSenha, UnicodeDecodeError) as exc:
            raise ErroCertificadoA1(
                "Não foi possível recuperar a senha protegida do certificado (DPAPI). "
                "Ela só pode ser lida pelo mesmo usuário do Windows que a cadastrou. "
                f"Cadastre o certificado novamente. Detalhe: {type(exc).__name__}"
            ) from exc
    return _senha_env()


def protecao_da_senha(cert_dir: str | Path | None = None) -> Literal["dpapi", "env", "nenhuma"]:
    """Como a senha do cadastro local está guardada (sem revelá-la)."""
    pasta = resolver_diretorio_certificado(cert_dir)
    if (pasta / NOME_ARQUIVO_SENHA_DPAPI).is_file():
        return "dpapi"
    return "env" if _senha_env() else "nenhuma"


def _validar_uf_autor(uf_autor: str) -> str:
    uf_limpa = str(uf_autor).strip()
    if not uf_limpa.isdigit() or len(uf_limpa) != 2:
        raise ErroCertificadoA1(
            f"Código IBGE de UF inválido ('uf_autor' deve ter 2 dígitos numéricos): {uf_autor!r}"
        )
    return uf_limpa


def _validar_ambiente_sefaz(ambiente: int | str) -> Literal[1, 2]:
    if ambiente in (1, "1"):
        return 1
    if ambiente in (2, "2"):
        return 2
    raise ErroCertificadoA1(
        f"Ambiente SEFAZ inválido ('ambiente' deve ser 1=Produção ou 2=Homologação): {ambiente!r}"
    )


def _ambiente_de_env() -> Literal[1, 2]:
    bruto = os.environ.get("NFE_AMBIENTE", "homologacao").strip().lower()
    if bruto in ("1", "producao", "produção", "prod"):
        return 1
    return 2


def _uf_de_env() -> str:
    uf_env = os.environ.get("NFE_UF_AUTOR", "35").strip()
    return uf_env if (uf_env.isdigit() and len(uf_env) == 2) else "35"


def ler_config_sefaz_local(cert_dir: str | Path | None = None) -> ConfigCertificadoSEFAZ:
    """Lê `config.json` do diretório local ou retorna padrões combinados com o ambiente."""
    pasta = resolver_diretorio_certificado(cert_dir)
    arq_cfg = pasta / NOME_ARQUIVO_CONFIG_FIXO
    arq_pfx = pasta / NOME_ARQUIVO_PFX_FIXO
    arq_senha = pasta / NOME_ARQUIVO_SENHA_DPAPI

    uf_padrao = _uf_de_env()
    amb_padrao: Literal[1, 2] = _ambiente_de_env()

    if arq_cfg.is_file():
        try:
            dados = json.loads(arq_cfg.read_text(encoding="utf-8"))
            uf_lida = str(dados.get("uf_autor", uf_padrao)).strip()
            if uf_lida.isdigit() and len(uf_lida) == 2:
                uf_padrao = uf_lida
            amb_lido = dados.get("ambiente", amb_padrao)
            if amb_lido in (1, "1"):
                amb_padrao = 1
            elif amb_lido in (2, "2"):
                amb_padrao = 2
        except (OSError, ValueError, TypeError):
            pass

    if arq_pfx.is_file() and (arq_senha.is_file() or _senha_env()):
        origem: Literal["interface", "env", "nao_configurado"] = "interface"
    elif os.environ.get("NFE_CERT_PATH", "").strip():
        origem = "env"
    else:
        origem = "nao_configurado"

    return ConfigCertificadoSEFAZ(
        uf_autor=uf_padrao,
        ambiente=amb_padrao,
        origem=origem,
    )


def salvar_certificado_local(
    pfx_bytes: bytes,
    senha: str,
    uf_autor: str = "35",
    ambiente: Literal[1, 2] = 2,
    cert_dir: str | Path | None = None,
) -> tuple[MetadadosCertificado, ConfigCertificadoSEFAZ]:
    """Valida o par `.pfx + senha` em memória e salva localmente com nome fixo `certificado.pfx`."""
    metadados = extrair_metadados_certificado(pfx_bytes, senha)
    uf_validada = _validar_uf_autor(uf_autor)
    amb_validado = _validar_ambiente_sefaz(ambiente)

    pasta = resolver_diretorio_certificado(cert_dir)

    # Protege a senha ANTES de gravar qualquer coisa; sem DPAPI a senha não vai para o disco.
    senha_protegida: bytes | None = None
    if segredos.dpapi_disponivel():
        try:
            senha_protegida = segredos.proteger_senha(senha)
        except segredos.ErroProtecaoSenha as exc:
            raise ErroCertificadoA1(f"Não foi possível proteger a senha: {exc}") from exc

    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / NOME_ARQUIVO_PFX_FIXO).write_bytes(pfx_bytes)
    if senha_protegida is not None:
        (pasta / NOME_ARQUIVO_SENHA_DPAPI).write_bytes(senha_protegida)
    else:
        (pasta / NOME_ARQUIVO_SENHA_DPAPI).unlink(missing_ok=True)
    (pasta / NOME_ARQUIVO_SENHA_FIXO).unlink(missing_ok=True)  # nunca deixar texto puro
    (pasta / NOME_ARQUIVO_CONFIG_FIXO).write_text(
        json.dumps(
            {"uf_autor": uf_validada, "ambiente": amb_validado},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    config = ConfigCertificadoSEFAZ(
        uf_autor=uf_validada,
        ambiente=amb_validado,
        origem="interface",
    )
    return metadados, config


def atualizar_config_sefaz_local(
    uf_autor: str | None = None,
    ambiente: Literal[1, 2] | None = None,
    cert_dir: str | Path | None = None,
) -> ConfigCertificadoSEFAZ:
    """Atualiza `uf_autor` e/ou `ambiente` em `config.json` sem exigir novo upload do `.pfx`."""
    atual = ler_config_sefaz_local(cert_dir)
    nova_uf = _validar_uf_autor(uf_autor) if uf_autor is not None else atual.uf_autor
    novo_amb = _validar_ambiente_sefaz(ambiente) if ambiente is not None else atual.ambiente

    pasta = resolver_diretorio_certificado(cert_dir)
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / NOME_ARQUIVO_CONFIG_FIXO).write_text(
        json.dumps(
            {"uf_autor": nova_uf, "ambiente": novo_amb},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return ler_config_sefaz_local(cert_dir)


def remover_certificado_local(cert_dir: str | Path | None = None) -> bool:
    """Remove `certificado.pfx` e a senha (`senha.dpapi`/`senha.txt`); `True` se existiam."""
    pasta = resolver_diretorio_certificado(cert_dir)
    arq_pfx = pasta / NOME_ARQUIVO_PFX_FIXO
    arqs_senha = (pasta / NOME_ARQUIVO_SENHA_DPAPI, pasta / NOME_ARQUIVO_SENHA_FIXO)

    havia_certificado = arq_pfx.is_file() or any(a.is_file() for a in arqs_senha)
    arq_pfx.unlink(missing_ok=True)
    for arq in arqs_senha:
        arq.unlink(missing_ok=True)

    return havia_certificado


def carregar_certificado_configurado(
    cert_dir: str | Path | None = None,
) -> tuple[bytes, str, MetadadosCertificado, ConfigCertificadoSEFAZ]:
    """Carrega o certificado priorizando o cadastro via interface (`cert_dir`) e fallback `.env`."""
    pasta = resolver_diretorio_certificado(cert_dir)
    arq_pfx = pasta / NOME_ARQUIVO_PFX_FIXO
    config_local = ler_config_sefaz_local(cert_dir)

    if arq_pfx.is_file():
        try:
            pfx_bytes = arq_pfx.read_bytes()
        except OSError as exc:
            raise ErroCertificadoA1(
                f"Erro ao ler o certificado salvo localmente em '{pasta}': {exc}"
            ) from exc
        senha = _ler_senha_salva(pasta)
        if senha is None:
            raise ErroCertificadoA1(
                "Certificado cadastrado, mas a senha não está disponível: este sistema não tem "
                "DPAPI. Defina a variável de ambiente NFE_CERT_SENHA."
            )

        metadados = extrair_metadados_certificado(pfx_bytes, senha)
        config = ConfigCertificadoSEFAZ(
            uf_autor=config_local.uf_autor,
            ambiente=config_local.ambiente,
            origem="interface",
        )
        return pfx_bytes, senha, metadados, config

    pfx_bytes, senha, metadados = carregar_certificado_de_env()
    config = ConfigCertificadoSEFAZ(
        uf_autor=config_local.uf_autor,
        ambiente=config_local.ambiente,
        origem="env",
    )
    return pfx_bytes, senha, metadados, config


# --- Migração do formato antigo (`data/certificado/` com `senha.txt` em texto puro) -----------


def detectar_cadastro_legado(legado_dir: str | Path | None = None) -> Path | None:
    """Retorna a pasta antiga se ela tiver `certificado.pfx` + `senha.txt` (não lê o conteúdo)."""
    pasta = Path(legado_dir) if legado_dir is not None else DIRETORIO_CERTIFICADO_LEGADO
    try:
        tem_pfx = (pasta / NOME_ARQUIVO_PFX_FIXO).is_file()
        tem_senha = (pasta / NOME_ARQUIVO_SENHA_FIXO).is_file()
    except OSError:
        return None
    return pasta if (tem_pfx and tem_senha) else None


def _apagar_arquivo(caminho: Path) -> None:
    """Sobrescreve com zeros (melhor esforço) e apaga o arquivo."""
    try:
        tamanho = caminho.stat().st_size
        with caminho.open("r+b") as fh:
            fh.write(b"\0" * tamanho)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass
    caminho.unlink(missing_ok=True)


def migrar_cadastro_legado(
    legado_dir: str | Path | None = None,
    cert_dir: str | Path | None = None,
) -> tuple[MetadadosCertificado, ConfigCertificadoSEFAZ]:
    """Move o cadastro antigo para o novo local, protege a senha e apaga o `senha.txt` antigo.

    A senha é lida apenas em memória, nunca é impressa nem registrada em log. Em caso de erro
    (DPAPI indisponível, senha inválida), nada é apagado.
    """
    origem = detectar_cadastro_legado(legado_dir)
    if origem is None:
        raise ErroCertificadoA1("Nenhum cadastro no formato antigo foi encontrado.")
    destino = resolver_diretorio_certificado(cert_dir)
    if origem.resolve() == destino.resolve():
        raise ErroCertificadoA1("O cadastro antigo já está na pasta de destino.")
    if not segredos.dpapi_disponivel():
        raise ErroCertificadoA1(
            "Migração indisponível sem DPAPI (não é Windows). Defina NFE_CERT_SENHA e "
            "mova o .pfx manualmente; o senha.txt antigo não foi apagado."
        )

    try:
        pfx_bytes = (origem / NOME_ARQUIVO_PFX_FIXO).read_bytes()
        senha = (origem / NOME_ARQUIVO_SENHA_FIXO).read_text(encoding="utf-8")
    except OSError as exc:
        raise ErroCertificadoA1(f"Erro ao ler o cadastro antigo: {type(exc).__name__}") from exc

    cfg_antiga = ler_config_sefaz_local(origem)
    metadados, config = salvar_certificado_local(
        pfx_bytes,
        senha,
        uf_autor=cfg_antiga.uf_autor,
        ambiente=cfg_antiga.ambiente,
        cert_dir=destino,
    )
    del senha

    for nome in (NOME_ARQUIVO_SENHA_FIXO, NOME_ARQUIVO_PFX_FIXO, NOME_ARQUIVO_CONFIG_FIXO):
        arq = origem / nome
        if arq.is_file():
            _apagar_arquivo(arq)
    return metadados, config
