"""event_study.py — Fase 6 PARTE 1 ("EVENT STUDY — validez del concepto").

Distinto del backtest (portfolio_report.py): el backtest mide si UNA
ESTRATEGIA de trading (con TP/SL/sizing/comisiones) ganó dinero; el event
study mide si el EVENTO EN SÍ mueve el precio de forma no aleatoria —
la pregunta de fondo que AUDIT_LEAN.md §2.2.3 ya identificó como "la que
responde ¿existe un edge?", sobre car_results (CAR de cada evento,
poblado por backtest/populate_car_results.py), no sobre portfolio_trades.

MDE = 2.8 · σ / √n (80% potencia, bilateral α=0.05) — la MISMA fórmula y
constante que AUDIT_LEAN.md §2.2.3 ya usó para argumentar la viabilidad
del proyecto. Se reutiliza aquí literalmente, no se deriva de nuevo, para
que el número que se cite en el reporte final sea el mismo concepto medido
dos veces (una vez proyectado sobre n esperado en el audit, otra vez sobre
el n real que terminó habiendo) — no dos fórmulas distintas con el mismo
nombre.
"""
from __future__ import annotations

import numpy as np
from scipy import stats
from statsmodels.stats.multitest import multipletests

from pipeline.backtest.sample_split import date_bounds

MDE_POWER_CONSTANT = 2.8  # ver docstring del módulo — AUDIT_LEAN.md §2.2.3
SIGNIFICANCE_ALPHA = 0.05
MIN_N_FOR_ANY_STATISTIC = 3  # por debajo de esto, ni sigma tiene sentido
# Corrección por contrastes múltiples (hallazgo de auditoría): run_event_study
# testea una hipótesis (H0: media=0) POR CADA event_class simultáneamente. Sin
# corregir, a más clases más probabilidad de que alguna "salga significativa"
# por puro azar (el clásico problema de comparaciones múltiples) — con, p.ej.,
# 8 clases y α=0.05 sin corregir, la probabilidad de al menos un falso
# positivo puede superar el 30%. Benjamini-Hochberg/FDR (vs. Bonferroni, más
# conservador) porque aquí interesa controlar la PROPORCIÓN esperada de falsos
# positivos entre los resultados marcados significativos, no eliminar
# cualquier posibilidad de uno solo — con un número de clases pequeño (~5-10)
# Bonferroni penalizaría en exceso el poder estadístico ya limitado por el n
# real de eventos por clase.
MULTIPLE_COMPARISONS_METHOD = "fdr_bh"


def compute_mde(sigma: float, n: int) -> float | None:
    """Efecto mínimo detectable — ver docstring del módulo. None si n<=0."""
    if n <= 0 or sigma is None:
        return None
    return MDE_POWER_CONSTANT * sigma / np.sqrt(n)


def _fetch_car_by_class(conn, window_days: int, sample: str | None = None) -> dict[str, dict[str, list]]:
    """CAR por clase, con UNA observación por (empresa, D0) dentro de cada
    clase (BUGS_REPORT.md H-20): dos filings de la misma empresa el mismo día
    (un 8-K y su enmienda, p. ej.) tienen exactamente el mismo CAR, y
    contarlos dos veces inflaba n y la significancia. Se queda el de menor
    event_id, de forma determinista.

    Devuelve {clase: {"car": [...], "d0": [...], "scar": [...]}}; scar es el
    CAR estandarizado (car / (resid_std * sqrt(n_event_days))) o None si el
    CAR se guardó antes de existir esas columnas."""
    start, end = date_bounds(sample)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (e.event_class, e.ticker, e.d0_close_date)
                   e.event_class, e.d0_close_date, cr.car,
                   CASE WHEN cr.resid_std > 0 AND cr.n_event_days > 0
                        THEN cr.car / (cr.resid_std * sqrt(cr.n_event_days)) END AS scar
            FROM car_results cr
            JOIN events e ON e.event_id = cr.event_id
            WHERE cr.window_days = %(window_days)s
              AND (%(start)s::date IS NULL OR e.d0_close_date >= %(start)s)
              AND (%(end)s::date IS NULL OR e.d0_close_date <= %(end)s)
            ORDER BY e.event_class, e.ticker, e.d0_close_date, e.event_id
            """,
            {"window_days": window_days, "start": start, "end": end},
        )
        rows = cur.fetchall()
    by_class: dict[str, dict[str, list]] = {}
    for r in rows:
        datos = by_class.setdefault(r["event_class"], {"car": [], "d0": [], "scar": []})
        datos["car"].append(float(r["car"]))
        datos["d0"].append(r["d0_close_date"])
        datos["scar"].append(float(r["scar"]) if r["scar"] is not None else None)
    return by_class


# Test robusto (BUGS_REPORT.md H-19). Los CAR crudos tienen colas enormes
# (microcaps con ±300 %) y los eventos se amontonan en las mismas fechas
# (temporada de resultados): con un t-test simple el error estándar sale
# demasiado pequeño y la significancia, inflada. El contraste principal es
# ahora un t sobre CAR winsorizados con errores agrupados por MES de D0; el
# t-test simple se conserva al lado para comparar.
#
# Por mes y no por día: el CAR a 20 días de dos eventos separados por un día
# comparte 19 de sus 20 días, así que agrupar por día exacto seguía tratando
# como independientes ventanas casi idénticas. Por mes queda solapamiento en
# las fronteras entre meses, pero mucho menor; es la opción conservadora.
WINSOR_PCT = 1.0  # recorte de colas: percentiles 1 y 99
# Por debajo de este n, el percentil 1 cae entre las dos observaciones más
# extremas y winsorizar solo deformaría la muestra: no se winsoriza.
WINSOR_MIN_N = 100


def _winsorize(arr: np.ndarray) -> np.ndarray:
    if len(arr) < WINSOR_MIN_N:
        return arr
    lo, hi = np.percentile(arr, [WINSOR_PCT, 100 - WINSOR_PCT])
    return np.clip(arr, lo, hi)


def _mes(d) -> object:
    """Grupo de un evento: el mes de su D0 (ver la nota de arriba)."""
    return (d.year, d.month) if hasattr(d, "year") else d


def clustered_mean_test(values: np.ndarray, clusters: list) -> tuple[float | None, float | None, int]:
    """t de la media contra 0 con errores agrupados por `clusters` (mes de
    D0): var(media) = G/(G-1) · Σ_g (Σ_{i∈g} e_i)² / n², con G-1 grados de
    libertad. Con un evento por fecha coincide exactamente con el t-test
    simple. Devuelve (t, p, G); t y p None si no es computable."""
    n = len(values)
    grupos: dict = {}
    for v, c in zip(values, clusters):
        grupos.setdefault(c, []).append(v)
    g = len(grupos)
    if n < MIN_N_FOR_ANY_STATISTIC or g < 2:
        return None, None, g
    mean = float(values.mean())
    suma_sq = sum((sum(vs) - len(vs) * mean) ** 2 for vs in grupos.values())
    var = g / (g - 1) * suma_sq / n**2
    if var <= 0:
        return None, None, g
    t = mean / np.sqrt(var)
    p = 2 * stats.t.sf(abs(t), df=g - 1)
    return float(t), float(p), g


def bmp_test(scar_values: list) -> tuple[float | None, float | None, int]:
    """Test de Boehmer, Musumeci y Poulsen (1991) sobre CAR estandarizados
    (CAR / (desviación de los residuos de la ventana de estimación · √L)):
    t = media(SCAR) · √n / desviación(SCAR). Pondera cada evento por la
    precisión de su modelo y es robusto al aumento de varianza en el evento.
    Versión sin el ajuste por error de predicción de la ventana de evento.
    Solo usa los eventos que tienen resid_std; devuelve (t, p, n_usados).

    Ojo: esos eventos son una SUBMUESTRA sesgada (los que conservan precios
    de la ventana de estimación tras ops_prune y cuyo CAR sigue cuadrando),
    así que el BMP no es directamente comparable con el contraste principal:
    es una comprobación de robustez, no un sustituto."""
    arr = np.array([v for v in scar_values if v is not None], dtype=float)
    n = len(arr)
    if n < MIN_N_FOR_ANY_STATISTIC:
        return None, None, n
    sd = arr.std(ddof=1)
    if sd == 0:
        return None, None, n
    t = arr.mean() * np.sqrt(n) / sd
    p = 2 * stats.t.sf(abs(t), df=n - 1)
    return float(t), float(p), n


def compute_event_study_for_class(car_values: list[float], d0_dates: list | None = None,
                                  scar_values: list | None = None) -> dict:
    """Estadísticos de una sola clase de evento: media, mediana,
    percentiles, sigma, MDE y el contraste H0: media = 0.

    t_statistic / p_value / significant son el contraste PRINCIPAL: t sobre
    CAR winsorizados (con n >= WINSOR_MIN_N) con errores agrupados por mes
    de D0 (H-19); mean_winsorized_pct es la media que ese contraste prueba. Sin fechas, cada evento es su propio grupo y el resultado
    es el t-test simple. Al lado: el t-test simple de siempre
    (t_statistic_simple / p_value_simple) y el BMP sobre los eventos con
    CAR estandarizado (t_statistic_bmp / p_value_bmp / n_bmp)."""
    n = len(car_values)
    vacio = {
        "t_statistic_simple": None, "p_value_simple": None, "n_clusters": None,
        "winsorized": False, "mean_winsorized_pct": None,
        "t_statistic_bmp": None, "p_value_bmp": None, "n_bmp": 0,
    }
    if n < MIN_N_FOR_ANY_STATISTIC:
        return {
            "n": n, "mean_return_pct": None, "median_return_pct": None,
            "p25_pct": None, "p75_pct": None, "sigma_pct": None, "mde_pct": None,
            "t_statistic": None, "p_value": None, "significant": None,
            "p_value_bh_adjusted": None, "significant_bh": None, **vacio,
            "conclusion": f"n={n} — muestra insuficiente para cualquier estadístico (mínimo {MIN_N_FOR_ANY_STATISTIC})",
        }

    arr = np.array(car_values) * 100  # a puntos porcentuales
    mean = float(arr.mean())
    median = float(np.median(arr))
    sigma = float(arr.std(ddof=1))
    p25, p75 = (float(x) for x in np.percentile(arr, [25, 75]))
    mde = compute_mde(sigma, n)
    clusters = [_mes(d) for d in d0_dates] if d0_dates is not None else list(range(n))
    wins = _winsorize(arr)
    t_bmp, p_bmp, n_bmp = bmp_test(scar_values or [])

    if sigma == 0.0:
        # Varianza cero (todos los CAR de la clase son idénticos — con n
        # pequeño y datos reales puede pasar de verdad, no solo en
        # fixtures de test). t = media/(sigma/√n) es una división por 0:
        # scipy devuelve Infinity/NaN, que ni siquiera es JSON válido
        # (Postgres lo rechaza al persistir en validation_reports — se
        # encontró exactamente así, con el pipeline nocturno real). No hay
        # una "significancia" que testear sobre una muestra sin dispersión;
        # se reporta como no computable, no como un número inventado.
        t_stat, p_value, significant = None, None, None
        t_simple, p_simple, n_clusters = None, None, len(set(clusters))
        conclusion = f"n={n} — varianza cero en la muestra (todos los CAR idénticos), t-test no aplicable"
    else:
        t_simple, p_simple = (float(x) for x in stats.ttest_1samp(arr, popmean=0.0))
        t_stat, p_value, n_clusters = clustered_mean_test(wins, clusters)
        significant = None if p_value is None else p_value < SIGNIFICANCE_ALPHA
        if significant is None:
            if n_clusters < 2:
                conclusion = f"n={n} — todos los eventos en el mismo mes, sin contraste agrupado posible"
            else:
                conclusion = f"n={n} — sin dispersión entre meses tras winsorizar, contraste agrupado no aplicable"

    if significant is True:
        conclusion = f"Significativo (p={p_value:.4f} < {SIGNIFICANCE_ALPHA}) — el evento SÍ mueve el precio de forma no aleatoria"
    elif significant is False:
        detectable_note = f"MDE={mde:.0f} bps con esta n" if mde is not None else ""
        conclusion = f"No significativo (p={p_value:.4f} >= {SIGNIFICANCE_ALPHA}) — {detectable_note}, podría ser ruido o un efecto real más pequeño que el MDE"

    return {
        "n": n,
        "mean_return_pct": mean,
        "median_return_pct": median,
        "p25_pct": p25,
        "p75_pct": p75,
        "sigma_pct": sigma,
        "mde_pct": mde,
        "t_statistic": t_stat,
        "p_value": p_value,
        "significant": significant,
        # Rellenados por run_event_study() (requiere ver TODAS las clases a
        # la vez) — None aquí porque esta función solo ve una clase.
        "p_value_bh_adjusted": None,
        "significant_bh": None,
        "t_statistic_simple": t_simple,
        "p_value_simple": p_simple,
        "n_clusters": n_clusters,
        "winsorized": len(arr) >= WINSOR_MIN_N,
        "mean_winsorized_pct": float(wins.mean()),
        "t_statistic_bmp": t_bmp,
        "p_value_bmp": p_bmp,
        "n_bmp": n_bmp,
        "conclusion": conclusion,
    }


def _apply_multiple_comparisons_correction(by_class_results: dict[str, dict]) -> None:
    """Corrige por contrastes múltiples IN-PLACE sobre los resultados de
    TODAS las clases a la vez (ver MULTIPLE_COMPARISONS_METHOD arriba) —
    no se puede hacer clase por clase, por definición: la corrección depende
    de cuántas hipótesis se testean simultáneamente. Las clases con p_value
    None (n insuficiente o varianza cero) se excluyen del ajuste — no hay
    p-valor que corregir — y quedan con p_value_bh_adjusted=significant_bh=None."""
    classes_with_pvalue = [c for c, r in by_class_results.items() if r["p_value"] is not None]
    if not classes_with_pvalue:
        return
    raw_pvalues = [by_class_results[c]["p_value"] for c in classes_with_pvalue]
    rejected, adjusted_pvalues, _, _ = multipletests(raw_pvalues, alpha=SIGNIFICANCE_ALPHA, method=MULTIPLE_COMPARISONS_METHOD)
    for event_class, is_significant_bh, p_adj in zip(classes_with_pvalue, rejected, adjusted_pvalues):
        result = by_class_results[event_class]
        result["p_value_bh_adjusted"] = float(p_adj)
        result["significant_bh"] = bool(is_significant_bh)
        if result["significant"] and not is_significant_bh:
            result["conclusion"] += (
                f" — ADVERTENCIA: no sobrevive a la corrección por contrastes múltiples "
                f"(p_bh={p_adj:.4f} >= {SIGNIFICANCE_ALPHA}, {len(classes_with_pvalue)} clases testeadas a la vez)"
            )


def run_event_study(conn, window_days: int = 20, sample: str | None = None) -> dict[str, dict]:
    """Punto de entrada — una fila de compute_event_study_for_class por
    event_class presente en car_results para la ventana dada. window_days=20
    por defecto (deriva histórica, no la reacción inmediata de 5 días) —
    mismo horizonte que AUDIT_LEAN.md §2.2.3 usa en su tabla de ejemplo.

    `sample`: None (default, sin filtro) / 'in_sample' / 'oos' — ver
    pipeline/backtest/sample_split.py. Se acota aquí también (no solo en el
    backtest de portfolio_report.py) para que un reporte de validación OOS
    no filtre información de la partición in-sample a través del Event
    Study (PARTE 1 del reporte): sin este filtro, un run con --oos habría
    mostrado igualmente el CAR calculado sobre TODOS los eventos, in-sample
    incluido, deshaciendo el propósito del holdout en la mitad del reporte.

    Corrección por contrastes múltiples (hallazgo de auditoría): con varias
    event_class testeadas a la vez, cada resultado incluye además
    p_value_bh_adjusted / significant_bh (Benjamini-Hochberg/FDR sobre TODAS
    las clases de esta llamada) junto al p_value/significant crudos — ambos
    se conservan, no se sobreescriben, para que el reporte pueda mostrar
    tanto el resultado sin corregir como el corregido."""
    by_class = _fetch_car_by_class(conn, window_days, sample=sample)
    results = {
        event_class: compute_event_study_for_class(datos["car"], d0_dates=datos["d0"], scar_values=datos["scar"])
        for event_class, datos in by_class.items()
    }
    _apply_multiple_comparisons_correction(results)
    return results
