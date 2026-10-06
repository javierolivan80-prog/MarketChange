"""Ayuda de tests: el backtest lee la decisión de la regla histórica sin IA
(event_analyses.decision_sin_ia -> regla_historica, BUGS_REPORT.md H-06), no
las columnas de la IA. Los tests del simulador que siembran un análisis a
mano y solo prueban la mecánica de la cartera copian aquí su propia decisión
para que el backtest la vea igual que antes."""

COPIAR_A_REGLA_HISTORICA_SQL = """
UPDATE event_analyses SET decision_sin_ia = jsonb_build_object(
    'regla_historica', jsonb_build_object(
        'net_conviction', net_conviction,
        'confidence_in_conviction', confidence_in_conviction,
        'ev_conservative', ev_conservative,
        'ev_balanced', ev_balanced,
        'ev_aggressive', ev_aggressive,
        'decisiones', jsonb_build_object(
            'CONSERVATIVE', jsonb_build_object('trade_decision', trade_decision_conservative),
            'BALANCED', jsonb_build_object('trade_decision', trade_decision_balanced),
            'AGGRESSIVE', jsonb_build_object('trade_decision', trade_decision_aggressive)
        )
    )
)
WHERE event_id = %s
"""


def copiar_a_regla_historica(cur, event_id: int) -> None:
    cur.execute(COPIAR_A_REGLA_HISTORICA_SQL, (event_id,))
