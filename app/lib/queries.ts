// queries.ts — lecturas tipadas contra el schema de pipeline/db/schema.sql.
//
// Los nombres de campo aquí deben mantenerse en sincronía con ese fichero;
// no hay una capa de migración/ORM que lo garantice automáticamente en este
// POC (decisión deliberada de simplicidad — ver ARCHITECTURE_LEAN.md §2).
//
// El dashboard (Fase 5) no recalcula NINGUNA fórmula financiera del lado de
// TypeScript: lee report_json ya armado por portfolio_report.py y
// paper_trading/report.py (Sharpe/Sortino/Calmar, calibración, submétricas
// por tipo de evento, etc.) — reimplementar esas fórmulas aquí sería
// mantener dos fuentes de verdad para el mismo número.
import { getPool } from "./db";

export type StrategyVersion = "CONSERVATIVE" | "BALANCED" | "AGGRESSIVE";

export interface PortfolioTradeMetrics {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  avg_winner: number | null;
  avg_loser: number | null;
  expectancy: number | null;
  largest_win: number | null;
  largest_loss: number | null;
  consecutive_wins: number;
  consecutive_losses: number;
}

export interface PortfolioEquityMetrics {
  total_return: number | null;
  annual_return: number | null;
  max_drawdown: number | null;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  calmar_ratio: number | null;
  recovery_factor: number | null;
  final_balance: number | null;
}

export interface PortfolioEquityPoint {
  trade_date: string;
  balance: number;
}

export interface EventTypeMetric {
  event_type: string;
  n_trades: number;
  win_rate: number;
  avg_return: number;
  sharpe_per_trade: number | null;
  insufficient_sample: boolean;
}

export interface Calibration {
  n_trades: number;
  mean_predicted_pct: number | null;
  mean_actual_pct: number | null;
  calibration_score: number | null;
  meets_target: boolean | null;
}

export interface PredictionRegression {
  n_trades: number;
  r_squared: number | null;
  scatter: { predicted: number; actual: number }[];
}

export interface AsymmetryReport {
  n_trades: number;
  threshold_pct?: number;
  pct_reaching_positive_threshold: number | null;
  pct_reaching_negative_threshold: number | null;
  asymmetric_favoring_gains: boolean | null;
}

export interface SerializedPortfolioTrade {
  event_id: number;
  ticker: string | null;
  event_class: string | null;
  direction: string;
  entry_date: string;
  exit_date: string;
  exit_reason: string;
  pnl_pct: number;
  confidence: number;
  ev: number;
}

export interface ConfidenceBucket {
  bucket: string;
  confidence_min: number;
  confidence_max: number;
  n: number;
  hit_rate: number;
  mean_confidence: number;
}

export interface CalibrationDiagnostics {
  n: number;
  correlation: number | null;
  brier_score: number | null;
  ece: number | null;
  buckets: ConfidenceBucket[];
}

export interface TemporalStability {
  split_date: string;
  n_trades_before: number;
  n_trades_after: number;
  metrics_before: PortfolioTradeMetrics;
  metrics_after: PortfolioTradeMetrics;
  stable: boolean | null;
  warnings: string[];
}

export interface PortfolioVersionReport {
  version: StrategyVersion;
  run_batch_tag: string;
  trade_metrics: PortfolioTradeMetrics;
  equity_metrics: PortfolioEquityMetrics;
  equity_curve: PortfolioEquityPoint[];
  metrics_by_event_type: Record<string, EventTypeMetric>;
  confidence_calibration: CalibrationDiagnostics;
  calibration: Calibration;
  prediction_regression: PredictionRegression;
  asymmetry: AsymmetryReport;
  all_trades: SerializedPortfolioTrade[];
  top_10_winners: SerializedPortfolioTrade[];
  top_10_losers: SerializedPortfolioTrade[];
  no_lookahead_violations: string[];
  temporal_stability: TemporalStability | null;
}

export interface PortfolioBiasReport {
  n_total_tickers: number;
  n_delisted: number;
  survivorship_bias_pct: number | null;
  n_price_gaps: number;
  n_price_rows: number;
  data_gap_pct: number | null;
}

export interface PortfolioRecommendation {
  verdict: string;
  findings: string[];
}

/** null = sin partición (todo el rango disponible, comportamiento histórico
 * por defecto) · 'in_sample' / 'oos' = backtest acotado por fecha (ver
 * pipeline/backtest/sample_split.py) — portfolio_report.py deja explícito
 * cuál es para que un reporte OOS nunca se confunda con uno in-sample,
 * ni en el JSON ni en lo que renderiza el dashboard. */
export type SampleSplit = "in_sample" | "oos" | null;

export interface PortfolioReport {
  run_batch_tag: string;
  starting_capital: number;
  sample?: SampleSplit;
  oos_warning?: string;
  versions: Record<StrategyVersion, PortfolioVersionReport>;
  bias_report: PortfolioBiasReport;
  recommendation: PortfolioRecommendation;
}

/** El run_batch_tag más reciente con un reporte de cartera guardado
 * (portfolio_reports, backtest histórico completo — Fase 3/4). */
export async function getLatestPortfolioRunBatchTag(): Promise<string | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT run_batch_tag FROM portfolio_reports ORDER BY created_at DESC LIMIT 1`
  );
  return rows[0]?.run_batch_tag ?? null;
}

/** El reporte completo para un run_batch_tag — node-postgres deserializa
 * JSONB directamente a objeto JS con los números ya como number (Python los
 * serializó con json.dumps sobre floats, no son columnas NUMERIC de
 * Postgres, así que no hace falta parseFloat manual aquí). */
export async function getPortfolioReport(runBatchTag: string): Promise<PortfolioReport | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT report_json FROM portfolio_reports WHERE run_batch_tag = $1`,
    [runBatchTag]
  );
  return rows[0]?.report_json ?? null;
}

// ============================================================================
// Paper trading (Fase 4 — pipeline/paper_trading/report.py)
// ============================================================================

export interface PaperOpenPosition {
  event_id: number;
  ticker: string;
  event_class: string | null;
  direction: string;
  entry_date: string;
  entry_price: number;
  current_price: number | null;
  current_price_date: string | null;
  unrealized_pnl_pct: number | null;
}

export interface PaperClosedTrade {
  event_id: number;
  ticker: string;
  event_class: string | null;
  direction: string;
  entry_date: string;
  entry_price: number;
  exit_date: string;
  exit_price: number;
  exit_reason: string;
  status: string;
  pnl_pct: number;
}

export interface PaperPrediction {
  event_id: number;
  ticker: string;
  event_class: string | null;
  predicted_direction: string;
  predicted_magnitude: number;
  actual_move_5d: number | null;
  error: number | null;
  was_correct: boolean | null;
  confidence_given: number;
  calibration_check: string;
  was_traded: boolean;
}

export interface PaperAlert {
  type: "WARNING" | "CONGRATULATE";
  version: StrategyVersion;
  message: string;
  ticker?: string;
  event_id?: number;
}

export interface PaperBacktestComparison {
  available: boolean;
  comparable?: boolean;
  note?: string;
  historical_win_rate?: number;
  week_win_rate?: number;
  diff_pp?: number;
  matches_historical?: boolean;
  // Solo presente para AGGRESSIVE (IMPROVEMENT_PLAN.md M14): esa versión usa
  // una mecánica de salida distinta en paper trading que en el backtest
  // histórico — ver pipeline/paper_trading/report.py:compare_with_historical_backtest.
  caveat?: string | null;
}

export interface PaperVersionReport {
  version: StrategyVersion;
  n_open_positions: number;
  n_closed_trades: number;
  open_positions: PaperOpenPosition[];
  last_10_closed_trades: PaperClosedTrade[];
  cumulative_pnl_pct_week: number;
  trade_metrics: PortfolioTradeMetrics;
  predictions: PaperPrediction[];
  calibration: CalibrationDiagnostics;
  alerts: PaperAlert[];
  comparison_with_historical_backtest: PaperBacktestComparison;
  top_3_best_predicted: PaperPrediction[];
  top_3_worst_predicted: PaperPrediction[];
}

export interface PaperTradingReport {
  run_batch_tag: string;
  week_start: string;
  week_end: string;
  versions: Record<StrategyVersion, PaperVersionReport>;
}

/** El run_batch_tag más reciente con un reporte de paper trading guardado.
 * A diferencia de portfolio_reports, aquí el mismo tag se reutiliza noche
 * tras noche mientras dure la semana simulada (paper_trading/report.py:
 * run_batch_tag se deriva de la semana, no de la fecha de hoy) — por eso
 * "más reciente por created_at" sigue siendo correcto: cada UPDATE
 * refresca created_at. */
export async function getLatestPaperTradingRunBatchTag(): Promise<string | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT run_batch_tag FROM paper_trading_reports ORDER BY created_at DESC LIMIT 1`
  );
  return rows[0]?.run_batch_tag ?? null;
}

export async function getPaperTradingReport(runBatchTag: string): Promise<PaperTradingReport | null> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT report_json FROM paper_trading_reports WHERE run_batch_tag = $1`,
    [runBatchTag]
  );
  return rows[0]?.report_json ?? null;
}

// ============================================================================
// Validation report (Fase 6 — pipeline/validation/report.py, persistido vía
// persist_validation_report en validation_reports). Responde las preguntas
// que el resto del dashboard no responde: ¿el EVENTO en sí mueve el precio
// de forma no aleatoria? (event study, sobre TODOS los eventos, no solo los
// operados) y ¿el resultado es frágil a supuestos de coste/latencia/régimen?
// (sensitivity), rematado en un veredicto GREENLIGHT/YELLOWLIGHT/REDLIGHT.
// ============================================================================

export interface EventStudyClassResult {
  n: number;
  mean_return_pct: number | null;
  median_return_pct: number | null;
  p25_pct: number | null;
  p75_pct: number | null;
  sigma_pct: number | null;
  mde_pct: number | null;
  t_statistic: number | null;
  p_value: number | null;
  significant: boolean | null;
  conclusion: string;
}

export type EventStudy = Record<string, EventStudyClassResult>;

export interface SensitivityScenarioResult {
  n_trades: number;
  win_rate: number | null;
  total_return: number | null;
}

export interface Sensitivity {
  run_batch_tag: string;
  // BALANCED se añadió en el pipeline después (IMPROVEMENT_PLAN.md R13): un
  // reporte antiguo puede no traerla — de ahí Partial.
  scenarios: Partial<Record<StrategyVersion, Record<string, SensitivityScenarioResult>>>;
}

export interface VersionDecision {
  option: "A" | "B" | "C";
  label: string;
  recommendation: string;
  reasons: string[];
}

export interface ValidationReport {
  run_batch_tag: string;
  generated_at: string;
  sample?: SampleSplit;
  oos_warning?: string;
  event_study: EventStudy;
  sensitivity: Sensitivity;
  decisions: Record<StrategyVersion, VersionDecision>;
  best_version: StrategyVersion;
  best_decision: VersionDecision;
  bias_report: PortfolioBiasReport;
}

export async function getLatestValidationRunBatchTag(): Promise<string | null> {
  const pool = getPool();
  const { rows } = await pool.query(`SELECT run_batch_tag FROM validation_reports ORDER BY created_at DESC LIMIT 1`);
  return rows[0]?.run_batch_tag ?? null;
}

export async function getValidationReport(runBatchTag: string): Promise<ValidationReport | null> {
  const pool = getPool();
  const { rows } = await pool.query(`SELECT report_json FROM validation_reports WHERE run_batch_tag = $1`, [runBatchTag]);
  return rows[0]?.report_json ?? null;
}

// ============================================================================
// Largo plazo — análisis fundamental (pipeline/analyze/quality_score.py sobre
// pipeline/ingest/xbrl_fundamentals.py). Pregunta distinta a la del resto del
// dashboard: no "¿esta noticia mueve el precio?" sino "¿es este un buen
// negocio a un precio razonable?", sobre las cuentas anuales auditadas.
// ============================================================================

export interface QualityComponent {
  name: string;
  score: number | null;      // null = no calculable con los datos disponibles
  raw_value: number | null;
  explanation: string;       // texto ya redactado en Python — no se reformula aquí
}

export interface QualityScoreRow {
  cik: string;
  ticker: string;
  company_name: string | null;
  as_of_date: string;
  total_score: number | null;
  verdict: string;
  components: QualityComponent[];
  n_years: number;
  price_used: number | null;
}

/** La fecha de cálculo más reciente con notas guardadas. */
export async function getLatestQualityScoreDate(): Promise<string | null> {
  const pool = getPool();
  const { rows } = await pool.query(`SELECT max(as_of_date) AS d FROM quality_scores`);
  const d = rows[0]?.d;
  if (!d) return null;
  return d instanceof Date ? d.toISOString().slice(0, 10) : String(d);
}

/** Notas de esa fecha, de mejor a peor. Las empresas sin nota calculable
 * (total_score NULL) van al final: no son "malas", es que no hay datos. */
export async function getQualityScores(asOfDate: string): Promise<QualityScoreRow[]> {
  const pool = getPool();
  const { rows } = await pool.query(
    `
    SELECT qs.cik, u.ticker, u.company_name, qs.as_of_date, qs.total_score,
           qs.verdict, qs.components, qs.n_years, qs.price_used
    FROM quality_scores qs
    JOIN universe u ON u.cik = qs.cik
    WHERE qs.as_of_date = $1
    ORDER BY qs.total_score DESC NULLS LAST, u.ticker
    `,
    [asOfDate]
  );
  return rows.map((r) => ({
    cik: r.cik,
    ticker: r.ticker,
    company_name: r.company_name,
    as_of_date: r.as_of_date instanceof Date ? r.as_of_date.toISOString().slice(0, 10) : r.as_of_date,
    total_score: r.total_score !== null ? parseFloat(r.total_score) : null,
    verdict: r.verdict,
    components: r.components,
    n_years: r.n_years,
    price_used: r.price_used !== null ? parseFloat(r.price_used) : null,
  }));
}

// ============================================================================
// Tab 1 "All Signals" (Fase 5) — feed cronológico de eventos analizados,
// con o sin trade_decision, con filtros. A diferencia del resto de este
// archivo, esta query SÍ compone datos con SQL propio (no lee un
// report_json ya armado) porque no hay un reporte pre-calculado que cubra
// "todos los eventos alguna vez analizados" — portfolio_reports solo cubre
// los que SÍ se operaron en la versión BALANCED del backtest más reciente.
// ============================================================================

export interface SignalFeedFilters {
  ticker?: string;
  eventClass?: string;
  signal?: "LONG" | "SHORT" | "NO_TRADE";
  dateFrom?: string;
  dateTo?: string;
  minConfidence?: number;
  limit?: number;
  /** Versión cuya decisión se muestra en la columna "Señal". */
  version?: StrategyVersion;
}

// Formas exactas de los campos JSONB de event_analyses — deben mantenerse en
// sincronía con los schemas/as_json() de pipeline/analyze/*.py (ver ahí para
// el porqué de cada campo). Sin esto, el dashboard solo puede mostrar los
// pocos campos que ya tenía columna propia — el resto del razonamiento
// (por qué NO_TRADE, qué dijo Bear, cuántos análogos había) queda calculado
// pero invisible, que es justo el hueco que esto cierra.
export interface BullOutput {
  thesis: string;
  upside_drivers: string[];
  addressable_market: string;
  comparable_events: string;
  catalysts_forward: string[];
}

export interface BearOutput {
  counter_thesis: string;
  downside_risks: string[];
  valuation_concern: string;
  historical_precedent: string;
  negative_catalysts: string[];
}

export interface JudgeOutput {
  net_conviction: number;
  confidence_in_conviction: number;
  key_uncertainty: string;
  overriding_concern: string;
}

export interface NoveltyReasoning {
  score: number;
  components_used: string[];
  components_unavailable: string[];
  [key: string]: unknown; // reasoning dict de novelty.py se aplana aquí (drift_pct, etc.)
}

export interface ImpactEstimation {
  probability_5pct_move: number;
  probability_10pct_move: number;
  probability_20pct_move: number;
  expected_direction: number;
  expected_magnitude: string; // "±X.XX% según histórico de N eventos análogos"
  volatility_increase: number;
  confidence: number;
}

export interface EvCalculation {
  ev_conservative: number;
  ev_aggressive: number;
  ev_balanced: number;
  position_sizing_conservative: string;
  position_sizing_aggressive: string;
  position_sizing_balanced: string;
  threshold_conservative: string;
  threshold_aggressive: string;
  threshold_balanced: string;
  reasoning: string;
}

export interface AbstentionPerStrategy {
  trade_decision: "LONG" | "SHORT" | "NO_TRADE";
  reason_if_no_trade: string | null;
  confidence: number;
}

export type AbstentionDecision = Record<"CONSERVATIVE" | "BALANCED" | "AGGRESSIVE", AbstentionPerStrategy>;

/** Fila LIGERA del feed: lo que la tabla pinta, sin el razonamiento JSONB del
 * LLM. Antes el feed mandaba hasta 500 filas con Bull/Bear/Judge/EV completos
 * dentro del HTML aunque el usuario no abriera ninguna; el detalle se pide
 * ahora bajo demanda (getSignalDetail, vía /api/senales/[id] o /senales/[id]). */
export interface SignalFeedRow {
  event_id: number;
  ticker: string;
  event_class: string;
  source: string;
  d0_close_date: string;
  novelty_score: number;
  net_conviction: number;
  confidence: number;
  ev_balanced: number;
  /** Decisión de la versión mostrada (`shown_version`): la que recomienda el
   * motor de validación, o BALANCED si todavía no hay informe. Antes se
   * derivaba del signo de net_conviction en cuanto CUALQUIER versión operaba,
   * así que la tabla podía decir "Long" para una señal que la versión
   * recomendada descartaba. */
  signal: "LONG" | "SHORT" | "NO_TRADE";
  shown_version: StrategyVersion;
  trade_decision_conservative: string;
  trade_decision_aggressive: string;
  trade_decision_balanced: string;
  entry_date: string | null;
  exit_date: string | null;
  exit_reason: string | null;
  pnl_pct: number | null;
}

export interface SignalDetailData extends SignalFeedRow {
  source_url: string;
  filed_at: string;
  analyzed_at: string;
  model_version_bull_bear: string;
  model_version_judge: string;
  novelty_reasoning: NoveltyReasoning | null;
  bull_output: BullOutput | null;
  bear_output: BearOutput | null;
  judge_output: JudgeOutput | null;
  impact_estimation: ImpactEstimation | null;
  n_historical_analogues: number | null;
  ev_calculation: EvCalculation | null;
  abstention_decision: AbstentionDecision | null;
  company_name: string | null;
}

export async function getEventClasses(): Promise<string[]> {
  const pool = getPool();
  const { rows } = await pool.query(`SELECT DISTINCT event_class FROM events ORDER BY event_class`);
  return rows.map((r) => r.event_class);
}

const VERSION_COLUMN: Record<StrategyVersion, string> = {
  CONSERVATIVE: "ea.trade_decision_conservative",
  BALANCED: "ea.trade_decision_balanced",
  AGGRESSIVE: "ea.trade_decision_aggressive",
};

function toDateStr(v: unknown): string | null {
  if (v === null || v === undefined) return null;
  return v instanceof Date ? v.toISOString().slice(0, 10) : String(v);
}

function toIso(v: unknown): string {
  return v instanceof Date ? v.toISOString() : String(v);
}

// Columnas comunes a feed y detalle. `$1` es siempre la versión mostrada.
// pt: solo la corrida de cartera más reciente — portfolio_trades guarda una
// fila por (event_id, version, run_batch_tag) y el pipeline escribe un
// run_batch_tag nuevo cada día sin borrar los anteriores; sin ese filtro
// cada evento operado salía repetido una vez por corrida histórica.
function signalSelect(version: StrategyVersion, extraColumns: string): string {
  return `
      SELECT
        e.event_id, e.ticker, e.event_class, e.source, e.d0_close_date,
        ea.novelty_score, ea.net_conviction, ea.confidence_in_conviction AS confidence, ea.ev_balanced,
        ea.trade_decision_conservative, ea.trade_decision_aggressive, ea.trade_decision_balanced,
        ${VERSION_COLUMN[version]} AS value,
        pt.entry_date, pt.exit_date, pt.exit_reason, pt.pnl_pct
        ${extraColumns}
      FROM events e
      JOIN event_analyses ea ON ea.event_id = e.event_id
      LEFT JOIN portfolio_trades pt ON pt.event_id = e.event_id AND pt.version = $1
        AND pt.run_batch_tag = (SELECT run_batch_tag FROM portfolio_reports ORDER BY created_at DESC LIMIT 1)`;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapFeedRow(r: any, version: StrategyVersion): SignalFeedRow {
  return {
    event_id: Number(r.event_id),
    ticker: r.ticker,
    event_class: r.event_class,
    source: r.source,
    d0_close_date: toDateStr(r.d0_close_date) ?? "",
    novelty_score: r.novelty_score,
    net_conviction: parseFloat(r.net_conviction),
    confidence: parseFloat(r.confidence),
    ev_balanced: parseFloat(r.ev_balanced),
    signal: r.value,
    shown_version: version,
    trade_decision_conservative: r.trade_decision_conservative,
    trade_decision_aggressive: r.trade_decision_aggressive,
    trade_decision_balanced: r.trade_decision_balanced,
    entry_date: toDateStr(r.entry_date),
    exit_date: toDateStr(r.exit_date),
    exit_reason: r.exit_reason,
    pnl_pct: r.pnl_pct !== null && r.pnl_pct !== undefined ? parseFloat(r.pnl_pct) : null,
  };
}

export async function getSignalsFeed(filters: SignalFeedFilters): Promise<SignalFeedRow[]> {
  const pool = getPool();
  const version = filters.version ?? "BALANCED";
  const params: unknown[] = [version];
  const conditions: string[] = [];

  if (filters.ticker) {
    params.push(`%${filters.ticker.toUpperCase()}%`);
    conditions.push(`signal.ticker ILIKE $${params.length}`);
  }
  if (filters.eventClass) {
    params.push(filters.eventClass);
    conditions.push(`signal.event_class = $${params.length}`);
  }
  if (filters.dateFrom) {
    params.push(filters.dateFrom);
    conditions.push(`signal.d0_close_date >= $${params.length}`);
  }
  if (filters.dateTo) {
    params.push(filters.dateTo);
    conditions.push(`signal.d0_close_date <= $${params.length}`);
  }
  if (filters.minConfidence !== undefined && Number.isFinite(filters.minConfidence)) {
    params.push(filters.minConfidence);
    conditions.push(`signal.confidence >= $${params.length}`);
  }
  if (filters.signal) {
    params.push(filters.signal);
    conditions.push(`signal.value = $${params.length}`);
  }
  params.push(Math.min(Math.max(filters.limit ?? 200, 1), 1000));
  const limitParam = `$${params.length}`;

  const { rows } = await pool.query(
    `
    SELECT * FROM (${signalSelect(version, "")}) signal
    ${conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : ""}
    ORDER BY signal.d0_close_date DESC, signal.event_id DESC
    LIMIT ${limitParam}
    `,
    params
  );
  return rows.map((r) => mapFeedRow(r, version));
}

/** Todo lo que el pipeline calculó para un evento: el razonamiento completo. */
export async function getSignalDetail(eventId: number, version: StrategyVersion = "BALANCED"): Promise<SignalDetailData | null> {
  if (!Number.isSafeInteger(eventId) || eventId <= 0) return null;
  const pool = getPool();
  const { rows } = await pool.query(
    `${signalSelect(
      version,
      `, e.source_url, e.filed_at, ea.analyzed_at, ea.model_version_bull_bear, ea.model_version_judge,
         ea.novelty_reasoning, ea.bull_analyst_output AS bull_output, ea.bear_analyst_output AS bear_output,
         ea.judge_output, ea.impact_estimation, ea.n_historical_analogues, ea.ev_calculation, ea.abstention_decision,
         u.company_name`
    )}
      LEFT JOIN universe u ON u.cik = e.cik
      WHERE e.event_id = $2`,
    [version, eventId]
  );
  const r = rows[0];
  if (!r) return null;
  return {
    ...mapFeedRow(r, version),
    source_url: r.source_url,
    filed_at: toIso(r.filed_at),
    analyzed_at: toIso(r.analyzed_at),
    model_version_bull_bear: r.model_version_bull_bear,
    model_version_judge: r.model_version_judge,
    novelty_reasoning: r.novelty_reasoning,
    bull_output: r.bull_output,
    bear_output: r.bear_output,
    judge_output: r.judge_output,
    impact_estimation: r.impact_estimation,
    n_historical_analogues: r.n_historical_analogues,
    ev_calculation: r.ev_calculation,
    abstention_decision: r.abstention_decision,
    company_name: r.company_name ?? null,
  };
}

// ============================================================================
// Inicio, historial y abstenciones — lecturas ligeras
// ============================================================================

const VERSIONS: readonly StrategyVersion[] = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"];

function asVersion(v: unknown): StrategyVersion {
  return VERSIONS.includes(v as StrategyVersion) ? (v as StrategyVersion) : "BALANCED";
}

/** La versión que el motor de validación recomienda (best_version del último
 * informe), o BALANCED si todavía no hay ninguno. Es la versión que la app
 * enseña por defecto: al usuario se le da una recomendación, no tres. */
export async function getRecommendedVersion(): Promise<StrategyVersion> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT report_json->>'best_version' AS v FROM validation_reports ORDER BY created_at DESC LIMIT 1`
  );
  return asVersion(rows[0]?.v);
}

export interface HomeSummary {
  portfolio: {
    sample: SampleSplit | null;
    oos_warning: string | null;
    trade_metrics: PortfolioTradeMetrics | null;
    equity_metrics: PortfolioEquityMetrics | null;
  } | null;
  validation: { best_version: StrategyVersion; best_decision: VersionDecision } | null;
  open_paper_positions: number | null;
}

/** Lo que Inicio necesita, extraído en SQL con rutas JSON en vez de bajar los
 * report_json completos (que incluyen todas las operaciones y curvas de las 3
 * versiones) para pintar cuatro cifras. */
export async function getHomeSummary(version: StrategyVersion): Promise<HomeSummary> {
  const pool = getPool();
  const [portfolio, validation, paper] = await Promise.all([
    pool.query(
      `SELECT report_json->'sample' AS sample, report_json->>'oos_warning' AS oos_warning,
              report_json->'versions'->$1->'trade_metrics' AS trade_metrics,
              report_json->'versions'->$1->'equity_metrics' AS equity_metrics
       FROM portfolio_reports ORDER BY created_at DESC LIMIT 1`,
      [version]
    ),
    pool.query(
      `SELECT report_json->>'best_version' AS best_version, report_json->'best_decision' AS best_decision
       FROM validation_reports ORDER BY created_at DESC LIMIT 1`
    ),
    pool.query(
      `SELECT (SELECT sum((v->>'n_open_positions')::int) FROM jsonb_each(report_json->'versions') AS x(k, v)) AS n_open
       FROM paper_trading_reports ORDER BY created_at DESC LIMIT 1`
    ),
  ]);
  const p = portfolio.rows[0];
  const v = validation.rows[0];
  const pp = paper.rows[0];
  return {
    portfolio: p
      ? { sample: p.sample ?? null, oos_warning: p.oos_warning ?? null, trade_metrics: p.trade_metrics ?? null, equity_metrics: p.equity_metrics ?? null }
      : null,
    validation: v?.best_decision ? { best_version: asVersion(v.best_version), best_decision: v.best_decision } : null,
    open_paper_positions: pp && pp.n_open !== null ? Number(pp.n_open) : null,
  };
}

export interface RecentSignal {
  event_id: number;
  ticker: string;
  event_class: string;
  d0_close_date: string;
  direction: "LONG" | "SHORT";
  confidence: number;
  ev_balanced: number;
}

/** Últimas señales en las que la versión mostrada decidió operar. */
export async function getRecentTradeSignals(version: StrategyVersion, limit = 5): Promise<RecentSignal[]> {
  const pool = getPool();
  const { rows } = await pool.query(
    `
    SELECT e.event_id, e.ticker, e.event_class, e.d0_close_date,
           ${VERSION_COLUMN[version]} AS direction,
           ea.confidence_in_conviction AS confidence, ea.ev_balanced
    FROM events e
    JOIN event_analyses ea ON ea.event_id = e.event_id
    WHERE ${VERSION_COLUMN[version]} != 'NO_TRADE'
    ORDER BY e.d0_close_date DESC, ea.confidence_in_conviction DESC
    LIMIT $1
    `,
    [limit]
  );
  return rows.map((r) => ({
    event_id: Number(r.event_id),
    ticker: r.ticker,
    event_class: r.event_class,
    d0_close_date: toDateStr(r.d0_close_date) ?? "",
    direction: r.direction,
    confidence: parseFloat(r.confidence),
    ev_balanced: parseFloat(r.ev_balanced),
  }));
}

/** Cuándo se analizó el último evento y cuántos en las últimas 24h — sin
 * esto, un pipeline caído hace días se ve igual que uno que corrió anoche. */
export async function getPipelineFreshness(): Promise<{ last_analyzed_at: string | null; analyzed_last_24h: number }> {
  const pool = getPool();
  const { rows } = await pool.query(
    `SELECT max(analyzed_at) AS last, count(*) FILTER (WHERE analyzed_at > now() - interval '24 hours') AS n24 FROM event_analyses`
  );
  const last = rows[0]?.last;
  return {
    last_analyzed_at: last ? toIso(last) : null,
    analyzed_last_24h: Number(rows[0]?.n24 ?? 0),
  };
}

// Los motivos de abstención son texto libre con un prefijo fijo según la
// regla que disparó (pipeline/analyze/abstention_engine.py:decide_for_strategy).
// Se agrupan por ese prefijo; un motivo nuevo sin mapear cae en "other".
export type AbstentionCategory =
  | "priced_in"
  | "low_confidence"
  | "low_ev"
  | "delisting"
  | "contradictory"
  | "fda_unconfirmed"
  | "illiquid"
  | "other";

export interface AbstentionSummary {
  days: number;
  analyzed: number;
  traded: number;
  reasons: { category: AbstentionCategory; n: number }[];
}

export async function getAbstentionSummary(version: StrategyVersion, days = 7): Promise<AbstentionSummary> {
  const pool = getPool();
  const reasonPath = `ea.abstention_decision->'${version}'->>'reason_if_no_trade'`;
  const { rows } = await pool.query(
    `
    WITH recent AS (
      SELECT ${VERSION_COLUMN[version]} AS decision, ${reasonPath} AS reason
      FROM event_analyses ea
      WHERE ea.analyzed_at > now() - make_interval(days => $1)
    )
    SELECT
      (SELECT count(*) FROM recent) AS analyzed,
      (SELECT count(*) FROM recent WHERE decision != 'NO_TRADE') AS traded,
      category, count(*) AS n
    FROM (
      SELECT CASE
        WHEN reason LIKE 'novelty_score=%' THEN 'priced_in'
        WHEN reason LIKE 'confidence_in_conviction=%' THEN 'low_confidence'
        WHEN reason LIKE '|ev|=%' THEN 'low_ev'
        WHEN reason LIKE '%deslistado%' THEN 'delisting'
        WHEN reason LIKE 'datos contradictorios%' THEN 'contradictory'
        WHEN reason LIKE 'CRL de FDA%' THEN 'fda_unconfirmed'
        WHEN reason LIKE '%ilíquido%' OR reason LIKE '%liquidez%' THEN 'illiquid'
        ELSE 'other'
      END AS category
      FROM recent WHERE decision = 'NO_TRADE'
    ) c
    GROUP BY category
    ORDER BY n DESC
    `,
    [days]
  );
  // Sin descartes la consulta agrupada no devuelve filas: los totales se piden aparte.
  let analyzed = rows[0] ? Number(rows[0].analyzed) : 0;
  let traded = rows[0] ? Number(rows[0].traded) : 0;
  if (!rows[0]) {
    const t = await pool.query(
      `SELECT count(*) AS analyzed, count(*) FILTER (WHERE ${VERSION_COLUMN[version]} != 'NO_TRADE') AS traded
       FROM event_analyses ea WHERE ea.analyzed_at > now() - make_interval(days => $1)`,
      [days]
    );
    analyzed = Number(t.rows[0]?.analyzed ?? 0);
    traded = Number(t.rows[0]?.traded ?? 0);
  }
  return {
    days,
    analyzed,
    traded,
    reasons: rows.map((r) => ({ category: r.category as AbstentionCategory, n: Number(r.n) })),
  };
}

export interface HistoryRow {
  event_id: number;
  ticker: string;
  event_class: string;
  d0_close_date: string;
  analyzed_at: string;
  notified_at: string | null;
  direction: "LONG" | "SHORT";
  confidence: number;
  status: string | null;
  entry_date: string | null;
  exit_date: string | null;
  pnl_pct: number | null;
}

export interface SignalHistory {
  version: StrategyVersion;
  rows: HistoryRow[];
  n_closed: number;
  n_open: number;
  win_rate: number | null;
  avg_pnl_pct: number | null;
}

/** Historial hacia delante: cada señal que la versión mostrada decidió operar,
 * con la hora a la que se calculó (y se avisó), y su resultado en la
 * simulación en papel sobre precios reales posteriores — no el backtest. Es
 * el único número que no puede estar sobreajustado al pasado. */
export async function getSignalHistory(version: StrategyVersion, limit = 200): Promise<SignalHistory> {
  const pool = getPool();
  const { rows } = await pool.query(
    `
    SELECT e.event_id, e.ticker, e.event_class, e.d0_close_date, ea.analyzed_at, ea.notified_at,
           ${VERSION_COLUMN[version]} AS direction, ea.confidence_in_conviction AS confidence,
           pt.status, pt.entry_date, pt.exit_date, pt.pnl_pct
    FROM events e
    JOIN event_analyses ea ON ea.event_id = e.event_id
    LEFT JOIN LATERAL (
      SELECT status, entry_date, exit_date, pnl_pct
      FROM paper_trades p
      WHERE p.event_id = e.event_id AND p.version = $1
      ORDER BY p.updated_at DESC
      LIMIT 1
    ) pt ON TRUE
    WHERE ${VERSION_COLUMN[version]} != 'NO_TRADE'
    ORDER BY e.d0_close_date DESC, e.event_id DESC
    LIMIT $2
    `,
    [version, limit]
  );
  const mapped: HistoryRow[] = rows.map((r) => ({
    event_id: Number(r.event_id),
    ticker: r.ticker,
    event_class: r.event_class,
    d0_close_date: toDateStr(r.d0_close_date) ?? "",
    analyzed_at: toIso(r.analyzed_at),
    notified_at: r.notified_at ? toIso(r.notified_at) : null,
    direction: r.direction,
    confidence: parseFloat(r.confidence),
    status: r.status ?? null,
    entry_date: toDateStr(r.entry_date),
    exit_date: toDateStr(r.exit_date),
    pnl_pct: r.pnl_pct !== null && r.pnl_pct !== undefined ? parseFloat(r.pnl_pct) : null,
  }));
  const closed = mapped.filter((r) => r.status !== null && r.status !== "OPEN" && r.pnl_pct !== null);
  return {
    version,
    rows: mapped,
    n_closed: closed.length,
    n_open: mapped.filter((r) => r.status === "OPEN").length,
    win_rate: closed.length > 0 ? closed.filter((r) => (r.pnl_pct ?? 0) > 0).length / closed.length : null,
    avg_pnl_pct: closed.length > 0 ? closed.reduce((s, r) => s + (r.pnl_pct ?? 0), 0) / closed.length : null,
  };
}
