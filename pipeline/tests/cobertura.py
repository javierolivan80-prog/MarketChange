"""Ayuda de tests: desde la Tanda 8 cada operación lleva una pata contraria
en SPY (portfolio_simulator.py, «COBERTURA CON SPY») y sin apertura de SPY
no se opera. Los tests que solo prueban la mecánica de la acción siembran
un SPY PLANO: su pata no gana ni pierde, así que el movimiento cubierto es
el de la acción y lo único que cambia frente a antes son los costes de la
segunda pata y el efectivo que inmoviliza (2× nocional)."""

SPY_PLANO = 400.0


def sembrar_spy_plano(conn, precio: float = SPY_PLANO, desde: str = "2015-01-01", hasta: str = "2027-12-31") -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume)
            SELECT 'SPY', d::date, %(p)s, %(p)s, %(p)s, %(p)s, 1.0, 1000000
            FROM generate_series(%(desde)s::date, %(hasta)s::date, interval '1 day') d
            WHERE extract(isodow FROM d) < 6
            ON CONFLICT (ticker, trade_date) DO NOTHING
            """,
            {"p": precio, "desde": desde, "hasta": hasta},
        )
    conn.commit()


def sembrar_spy(conn, precios: dict, *, aperturas: dict | None = None) -> None:
    """Velas concretas de SPY (fecha -> cierre; apertura igual al cierre si
    no se da), sobrescribiendo las del SPY plano."""
    aperturas = aperturas or {}
    with conn.cursor() as cur:
        for d, cierre in precios.items():
            apertura = aperturas.get(d, cierre)
            cur.execute(
                """
                INSERT INTO prices (ticker, trade_date, open_raw, close_raw, high_raw, low_raw, adj_factor, volume)
                VALUES ('SPY', %s, %s, %s, %s, %s, 1.0, 1000000)
                ON CONFLICT (ticker, trade_date) DO UPDATE SET open_raw = EXCLUDED.open_raw,
                    close_raw = EXCLUDED.close_raw, high_raw = EXCLUDED.high_raw, low_raw = EXCLUDED.low_raw
                """,
                (d, apertura, cierre, max(apertura, cierre), min(apertura, cierre)),
            )
    conn.commit()
