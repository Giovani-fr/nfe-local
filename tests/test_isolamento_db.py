"""Garante que a suíte nunca usa o banco real (`data/nfe_recebidas.db`)."""

import os

from sefaz.storage import resolver_caminho_db
from tests.conftest import DB_PADRAO


def test_isolamento_db_usa_caminho_temporario(_db_temporario, tmp_path):
    resolvido = resolver_caminho_db().resolve()
    assert os.environ["NFE_DB_PATH"] == str(_db_temporario)
    assert resolvido != DB_PADRAO.resolve()
    assert tmp_path in resolvido.parents
