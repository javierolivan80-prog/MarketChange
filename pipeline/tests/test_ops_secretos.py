"""Comprobación de secretos (Tanda 5): dice cuáles faltan, nunca su valor."""
from pipeline.ops_secretos import SECRETOS, main, secretos_que_faltan


def test_detecta_los_que_faltan_y_los_vacios():
    entorno = {n: "x" for n in SECRETOS}
    entorno["TELEGRAM_CHAT_ID"] = "  "
    del entorno["BACKUP_PASSPHRASE"]
    assert set(secretos_que_faltan(entorno)) == {"TELEGRAM_CHAT_ID", "BACKUP_PASSPHRASE"}


def test_nunca_imprime_el_valor(monkeypatch, capsys):
    for n in SECRETOS:
        monkeypatch.setenv(n, f"valor-secreto-{n}")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    main()
    salida = capsys.readouterr().out
    assert "valor-secreto" not in salida
    assert "::warning title=Falta el secreto ANTHROPIC_API_KEY::" in salida
