// Pruebas de las consultas del panel contra un Postgres real con el mismo
// schema.sql que usa el pipeline. Se saltan si TEST_DATABASE_URL no está
// definida. ATENCIÓN: la base indicada se vacía (DROP SCHEMA public) — usar
// solo una base de pruebas.
import { after, before, beforeEach, describe, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { closePool, getPool } from "./db";
import {
  getAbstentionSummary,
  getHomeSummary,
  getRecentTradeSignals,
  getRecommendedVersion,
  getSignalDetail,
  getSignalHistory,
  getSignalsFeed,
} from "./queries";

const TEST_URL = process.env.TEST_DATABASE_URL;
const SCHEMA = join(__dirname, "..", "..", "pipeline", "db", "schema.sql");

type Decision = "LONG" | "SHORT" | "NO_TRADE";

let nextId = 0;

async function seedEvent(opts: {
  ticker: string;
  d0: string;
  decisions: [Decision, Decision, Decision]; // conservative, balanced, aggressive
  reasonBalanced?: string | null;
  analyzedHoursAgo?: number;
}): Promise<number> {
  const pool = getPool();
  nextId += 1;
  const cik = String(1000 + nextId);
  await pool.query(
    `INSERT INTO universe (cik, ticker, company_name, sic_code, first_seen_date, last_seen_date)
     VALUES ($1, $2, $3, '2836', $4, $4)`,
    [cik, opts.ticker, `${opts.ticker} Inc`, opts.d0]
  );
  const { rows } = await pool.query(
    `INSERT INTO events (cik, ticker, source, is_satellite, event_class, item_codes, accession_number, source_url,
       filed_at, d0_close_date, classification_method, classification_confidence, raw_text_hash)
     VALUES ($1, $2, 'EDGAR', FALSE, '8K_2.02_EARNINGS', ARRAY['2.02'], $3, 'https://www.sec.gov/x', $4::date::timestamptz, $4::date, 'RULE', 1.0, $3)
     RETURNING event_id`,
    [cik, opts.ticker, `acc-${nextId}`, opts.d0]
  );
  const eventId = Number(rows[0].event_id);
  const [c, b, a] = opts.decisions;
  const abstention = {
    CONSERVATIVE: { trade_decision: c, reason_if_no_trade: null, confidence: 70 },
    BALANCED: { trade_decision: b, reason_if_no_trade: opts.reasonBalanced ?? null, confidence: 70 },
    AGGRESSIVE: { trade_decision: a, reason_if_no_trade: null, confidence: 70 },
  };
  await pool.query(
    `INSERT INTO event_analyses (event_id, analyzed_at, novelty_score, novelty_reasoning, bull_analyst_output, bear_analyst_output,
       judge_output, net_conviction, confidence_in_conviction, impact_estimation, n_historical_analogues, ev_calculation,
       ev_conservative, ev_aggressive, ev_balanced, abstention_decision, trade_decision_conservative,
       trade_decision_aggressive, trade_decision_balanced, model_version_bull_bear, model_version_judge)
     VALUES ($1, now() - make_interval(hours => $2), 60, '{}', $3, '{}', $4, 0.4, 70, '{}', 5, '{}',
       0.01, 0.02, 0.015, $5, $6, $7, $8, 'm1', 'm2')`,
    [
      eventId,
      opts.analyzedHoursAgo ?? 1,
      JSON.stringify({ thesis: "Tesis alcista" }),
      JSON.stringify({ key_uncertainty: "Margen" }),
      JSON.stringify(abstention),
      c,
      a,
      b,
    ]
  );
  return eventId;
}

async function seedPortfolioRun(tag: string, daysAgo: number, eventIds: number[], report: object = { versions: {} }) {
  const pool = getPool();
  await pool.query(`INSERT INTO portfolio_reports (run_batch_tag, report_json, created_at) VALUES ($1, $2, now() - make_interval(days => $3))`, [
    tag,
    JSON.stringify(report),
    daysAgo,
  ]);
  for (const id of eventIds) {
    await pool.query(
      `INSERT INTO portfolio_trades (event_id, version, execution_style, direction, entry_date, entry_price, exit_date, exit_price,
         exit_reason, pnl_pct, pnl_abs, position_size_pct, position_size_dollars, confidence, ev, prediction, actual_move_pct, run_batch_tag)
       VALUES ($1, 'BALANCED', 'CONSERVATIVE', 'LONG', '2026-01-02', 10, '2026-01-09', 11, 'TAKE_PROFIT', $2, 10, 5, 500, 70, 0.01, 0.4, 10, $3)`,
      [id, daysAgo, tag]
    );
  }
}

describe("consultas del panel contra Postgres", { skip: !TEST_URL && "TEST_DATABASE_URL no definida" }, () => {
  before(async () => {
    process.env.DATABASE_URL = TEST_URL;
  });

  beforeEach(async () => {
    const pool = getPool();
    await pool.query("DROP SCHEMA public CASCADE; CREATE SCHEMA public;");
    await pool.query(readFileSync(SCHEMA, "utf8"));
  });

  after(async () => {
    await closePool();
  });

  test("el feed no repite un evento por cada corrida de cartera", async () => {
    const a = await seedEvent({ ticker: "AAA", d0: "2026-01-01", decisions: ["NO_TRADE", "LONG", "LONG"] });
    const b = await seedEvent({ ticker: "BBB", d0: "2026-01-02", decisions: ["NO_TRADE", "NO_TRADE", "SHORT"] });
    await seedPortfolioRun("run-1", 3, [a]);
    await seedPortfolioRun("run-2", 2, [a]);
    await seedPortfolioRun("run-3", 1, [a]);

    const rows = await getSignalsFeed({ limit: 50 });
    assert.deepEqual(rows.map((r) => r.event_id).sort(), [a, b].sort());
    // El resultado es el de la corrida más reciente (pnl = daysAgo = 1).
    assert.equal(rows.find((r) => r.event_id === a)?.pnl_pct, 1);
  });

  test("la columna Señal es la decisión de la versión mostrada", async () => {
    const id = await seedEvent({ ticker: "CCC", d0: "2026-01-01", decisions: ["NO_TRADE", "NO_TRADE", "LONG"] });
    const [balanced] = await getSignalsFeed({ version: "BALANCED" });
    const [aggressive] = await getSignalsFeed({ version: "AGGRESSIVE" });
    assert.equal(balanced.event_id, id);
    assert.equal(balanced.signal, "NO_TRADE");
    assert.equal(aggressive.signal, "LONG");
    assert.equal((await getSignalsFeed({ version: "BALANCED", signal: "LONG" })).length, 0);
  });

  test("detalle: JSON del LLM parseado, null si no existe", async () => {
    const id = await seedEvent({ ticker: "DDD", d0: "2026-01-01", decisions: ["LONG", "LONG", "LONG"] });
    const detail = await getSignalDetail(id, "BALANCED");
    assert.equal(detail?.bull_output?.thesis, "Tesis alcista");
    assert.equal(detail?.judge_output?.key_uncertainty, "Margen");
    assert.equal(detail?.company_name, "DDD Inc");
    assert.equal(await getSignalDetail(999999), null);
    assert.equal(await getSignalDetail(-1), null);
    assert.equal(detail?.ai_skipped_reason, null);
  });

  test("contrato con el pipeline: un descarte previo a la IA llega como tal, no como veredicto", async () => {
    // Forma exacta que escribe pipeline/analyze/event_analysis_pipeline.py
    // cuando una regla objetiva ya garantiza NO_TRADE. Con ella, la ficha
    // leía judge.net_conviction (inexistente) y la página se caía (#71).
    const id = await seedEvent({ ticker: "EEE", d0: "2026-01-01", decisions: ["NO_TRADE", "NO_TRADE", "NO_TRADE"] });
    const marker = JSON.stringify({ skipped_no_llm_needed: true, reason: "ADV < 1M$ — NO_TRADE garantizado, no se invoca el debate de IA" });
    await getPool().query(
      `UPDATE event_analyses SET bull_analyst_output = $2, bear_analyst_output = $2, judge_output = $2,
         model_version_bull_bear = 'SKIPPED_OBJECTIVE_NO_TRADE', model_version_judge = 'SKIPPED_OBJECTIVE_NO_TRADE'
       WHERE event_id = $1`,
      [id, marker]
    );
    const detail = await getSignalDetail(id, "BALANCED");
    assert.equal(detail?.ai_skipped_reason, "ADV < 1M$ — NO_TRADE garantizado, no se invoca el debate de IA");
    assert.equal(detail?.bull_output, null);
    assert.equal(detail?.bear_output, null);
    assert.equal(detail?.judge_output, null);
  });

  test("versión recomendada: la del último informe de validación, o BALANCED", async () => {
    assert.equal(await getRecommendedVersion(), "BALANCED");
    await getPool().query(`INSERT INTO validation_reports (run_batch_tag, report_json) VALUES ('v1', '{"best_version": "AGGRESSIVE"}')`);
    assert.equal(await getRecommendedVersion(), "AGGRESSIVE");
  });

  test("un OOS lanzado a mano no sustituye al informe de cada noche (H-07)", async () => {
    const pool = getPool();
    await pool.query(
      `INSERT INTO validation_reports (run_batch_tag, report_json, created_at) VALUES
         ('is', '{"sample": "in_sample", "best_version": "CONSERVATIVE"}', now() - interval '1 day'),
         ('oos', '{"sample": "oos", "best_version": "AGGRESSIVE"}', now())`
    );
    assert.equal(await getRecommendedVersion(), "CONSERVATIVE");
    const a = await seedEvent({ ticker: "OOS", d0: "2026-01-01", decisions: ["LONG", "LONG", "LONG"] });
    await seedPortfolioRun("is-run", 2, [a], { sample: "in_sample", versions: {} });
    await seedPortfolioRun("oos-run", 1, [a], { sample: "oos", versions: {} });
    const [row] = await getSignalsFeed({ limit: 5 });
    assert.equal(row.pnl_pct, 2); // el de la corrida in-sample (pnl = daysAgo)
  });

  test("resumen de abstenciones agrupado por motivo", async () => {
    await seedEvent({ ticker: "E1", d0: "2026-01-01", decisions: ["NO_TRADE", "NO_TRADE", "NO_TRADE"], reasonBalanced: "novelty_score=10 < 20 (evento completamente descontado por el mercado)" });
    await seedEvent({ ticker: "E2", d0: "2026-01-01", decisions: ["NO_TRADE", "NO_TRADE", "NO_TRADE"], reasonBalanced: "|ev|=0.10% < umbral balanced (1.0%) + buffer 50bps = 1.50% (EV insuficiente hasta después de fees)" });
    await seedEvent({ ticker: "E3", d0: "2026-01-01", decisions: ["NO_TRADE", "NO_TRADE", "NO_TRADE"], reasonBalanced: "|ev|=0.20% < umbral" });
    await seedEvent({ ticker: "E4", d0: "2026-01-01", decisions: ["LONG", "LONG", "LONG"] });
    await seedEvent({ ticker: "OLD", d0: "2025-01-01", decisions: ["NO_TRADE", "NO_TRADE", "NO_TRADE"], analyzedHoursAgo: 24 * 30 });

    const s = await getAbstentionSummary("BALANCED", 7);
    assert.equal(s.analyzed, 4);
    assert.equal(s.traded, 1);
    assert.deepEqual(s.reasons, [
      { category: "low_ev", n: 2 },
      { category: "priced_in", n: 1 },
    ]);
  });

  test("los eventos anteriores al corte (solo regla sin IA) no cuentan en el resumen de abstenciones", async () => {
    await seedEvent({ ticker: "N1", d0: "2026-01-01", decisions: ["NO_TRADE", "NO_TRADE", "NO_TRADE"], reasonBalanced: "|ev|=0.10% < umbral" });
    const viejo = await seedEvent({ ticker: "V1", d0: "2022-01-03", decisions: ["NO_TRADE", "NO_TRADE", "NO_TRADE"] });
    await getPool().query(
      `UPDATE event_analyses SET model_version_bull_bear = 'SIN_IA_ANTES_DEL_CORTE', model_version_judge = 'SIN_IA_ANTES_DEL_CORTE' WHERE event_id = $1`,
      [viejo]
    );
    const s = await getAbstentionSummary("BALANCED", 7);
    assert.equal(s.analyzed, 1);
    assert.deepEqual(s.reasons, [{ category: "low_ev", n: 1 }]);
  });

  test("resumen de abstenciones sin descartes devuelve los totales", async () => {
    await seedEvent({ ticker: "T1", d0: "2026-01-01", decisions: ["LONG", "LONG", "LONG"] });
    const s = await getAbstentionSummary("BALANCED", 7);
    assert.equal(s.analyzed, 1);
    assert.equal(s.traded, 1);
    assert.deepEqual(s.reasons, []);
  });

  test("historial: solo señales operadas, con el último estado en papel", async () => {
    const traded = await seedEvent({ ticker: "H1", d0: "2026-01-05", decisions: ["NO_TRADE", "LONG", "LONG"] });
    await seedEvent({ ticker: "H2", d0: "2026-01-06", decisions: ["NO_TRADE", "NO_TRADE", "LONG"] });
    const pool = getPool();
    for (const [tag, status, pnl, ago] of [
      ["w1", "OPEN", null, 2],
      ["w2", "CLOSED_TP", 3.5, 1],
    ] as const) {
      await pool.query(
        `INSERT INTO paper_trades (run_batch_tag, week_start, week_end, event_id, version, direction, entry_date, entry_price,
           exit_date, exit_price, exit_reason, status, pnl_pct, confidence, ev, prediction, updated_at)
         VALUES ($1, '2026-01-05', '2026-01-09', $2, 'BALANCED', 'LONG', '2026-01-06', 10, $3, $4, $5, $6, $7, 70, 0.01, 0.4,
           now() - make_interval(days => $8))`,
        [tag, traded, pnl === null ? null : "2026-01-08", pnl === null ? null : 11, pnl === null ? null : "TAKE_PROFIT", status, pnl, ago]
      );
    }
    const h = await getSignalHistory("BALANCED");
    assert.deepEqual(h.rows.map((r) => r.ticker), ["H1"]);
    assert.equal(h.rows[0].status, "CLOSED_TP");
    assert.equal(h.n_closed, 1);
    assert.equal(h.win_rate, 1);
    assert.equal(h.avg_pnl_pct, 3.5);
  });

  test("Inicio lee solo las rutas JSON que necesita", async () => {
    await seedPortfolioRun("run-1", 1, [], {
      sample: "in_sample",
      versions: {
        AGGRESSIVE: {
          trade_metrics: { total_trades: 7 },
          equity_metrics: { total_return: 0.1 },
          equity_curve: Array.from({ length: 300 }, (_, i) => ({ trade_date: `d${i}`, balance: 100 + i })),
        },
      },
    });
    await getPool().query(
      `INSERT INTO paper_trading_reports (run_batch_tag, week_start, week_end, report_json)
       VALUES ('p1', '2026-01-05', '2026-01-09', '{"versions": {"BALANCED": {"n_open_positions": 2}, "AGGRESSIVE": {"n_open_positions": 3}}}')`
    );
    const s = await getHomeSummary("AGGRESSIVE");
    assert.equal(s.portfolio?.sample, "in_sample");
    assert.equal(s.portfolio?.trade_metrics?.total_trades, 7);
    assert.equal(s.open_paper_positions, 5);
    assert.equal(s.validation, null);
    // Curva reducida: ~60 puntos, con el primero y el último.
    assert.ok(s.equity_spark.length >= 50 && s.equity_spark.length <= 62, String(s.equity_spark.length));
    assert.equal(s.equity_spark[0], 100);
    assert.equal(s.equity_spark[s.equity_spark.length - 1], 399);
    assert.equal((await getHomeSummary("BALANCED")).portfolio?.trade_metrics, null);
    assert.deepEqual((await getHomeSummary("BALANCED")).equity_spark, []);
  });

  test("plan técnico en feed, detalle y señales recientes", async () => {
    const id = await seedEvent({ ticker: "TEC", d0: "2026-01-01", decisions: ["NO_TRADE", "LONG", "LONG"] });
    const sinPlan = await seedEvent({ ticker: "SIN", d0: "2026-01-02", decisions: ["NO_TRADE", "LONG", "LONG"] });
    await getPool().query(
      `INSERT INTO technical_analyses (event_id, direction, entry_price, stop_price, target_price, target2_price, risk_reward,
         confidence, passes_filters, position_size_pct, timeframe_days, details)
       VALUES ($1, 'LONG', 100, 96.5, 108, 112, 2.29, 80, TRUE, 2.5, 8, $2)`,
      [id, JSON.stringify({ aligned: ["RSI", "MACD", "OBV"], checks: { risk_reward_ok: true } })]
    );

    const feed = await getSignalsFeed({});
    assert.equal(feed.find((r) => r.event_id === id)?.tech_confidence, 80);
    assert.equal(feed.find((r) => r.event_id === id)?.tech_passes, true);
    assert.equal(feed.find((r) => r.event_id === sinPlan)?.tech_confidence, null);

    const detail = await getSignalDetail(id);
    assert.equal(detail?.technical?.stop, 96.5);
    assert.equal(detail?.technical?.risk_reward, 2.29);
    assert.deepEqual(detail?.technical?.details.aligned, ["RSI", "MACD", "OBV"]);
    assert.equal((await getSignalDetail(sinPlan))?.technical, null);

    const recent = await getRecentTradeSignals("BALANCED");
    const tec = recent.find((r) => r.ticker === "TEC");
    assert.equal(tec?.entry, 100);
    assert.equal(tec?.target, 108);
    assert.equal(tec?.timeframe_days, 8);
    assert.equal(recent.find((r) => r.ticker === "SIN")?.entry, null);
  });

  test("últimas señales operables de la versión mostrada", async () => {
    await seedEvent({ ticker: "R1", d0: "2026-01-01", decisions: ["NO_TRADE", "LONG", "LONG"] });
    await seedEvent({ ticker: "R2", d0: "2026-01-02", decisions: ["NO_TRADE", "NO_TRADE", "SHORT"] });
    assert.deepEqual((await getRecentTradeSignals("BALANCED")).map((r) => r.ticker), ["R1"]);
    assert.deepEqual((await getRecentTradeSignals("AGGRESSIVE")).map((r) => [r.ticker, r.direction]), [
      ["R2", "SHORT"],
      ["R1", "LONG"],
    ]);
  });
});
