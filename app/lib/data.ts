// data.ts — las lecturas de queries.ts con caché de datos de Next.
//
// El pipeline escribe tres veces al día; todas las páginas eran force-dynamic
// sin ninguna caché, así que cada visita repetía de 3 a 6 consultas con
// JSONB grandes contra un Postgres de plan gratuito (pool de 3 conexiones
// por instancia). Con un TTL de minutos, el dato mostrado se retrasa como
// mucho ese TTL respecto a la base — irrelevante para un sistema nocturno — y
// el coste por visita pasa a ser casi cero.
//
// Los envoltorios viven aquí y no en queries.ts para que esas funciones se
// puedan probar contra Postgres fuera del runtime de Next (unstable_cache
// necesita el incrementalCache de Next y falla en un test con node --test).
import { unstable_cache } from "next/cache";
import * as q from "./queries";

const FIVE_MIN = 300;
const ONE_HOUR = 3600;

export const getRecommendedVersion = unstable_cache(q.getRecommendedVersion, ["recommended-version"], { revalidate: FIVE_MIN });
export const getHomeSummary = unstable_cache(q.getHomeSummary, ["home-summary"], { revalidate: FIVE_MIN });
export const getRecentTradeSignals = unstable_cache(q.getRecentTradeSignals, ["recent-trade-signals"], { revalidate: FIVE_MIN });
export const getPipelineFreshness = unstable_cache(q.getPipelineFreshness, ["pipeline-freshness"], { revalidate: 60 });
export const getAbstentionSummary = unstable_cache(q.getAbstentionSummary, ["abstention-summary"], { revalidate: FIVE_MIN });
export const getSignalHistory = unstable_cache(q.getSignalHistory, ["signal-history"], { revalidate: FIVE_MIN });
export const getEventClasses = unstable_cache(q.getEventClasses, ["event-classes"], { revalidate: ONE_HOUR });
export const getSignalsFeed = unstable_cache(q.getSignalsFeed, ["signals-feed"], { revalidate: FIVE_MIN });
export const getSignalDetail = unstable_cache(q.getSignalDetail, ["signal-detail"], { revalidate: FIVE_MIN });

export const getLatestPortfolioRunBatchTag = unstable_cache(q.getLatestPortfolioRunBatchTag, ["latest-portfolio-tag"], { revalidate: FIVE_MIN });
export const getLatestPaperTradingRunBatchTag = unstable_cache(q.getLatestPaperTradingRunBatchTag, ["latest-paper-tag"], { revalidate: FIVE_MIN });
export const getLatestValidationRunBatchTag = unstable_cache(q.getLatestValidationRunBatchTag, ["latest-validation-tag"], { revalidate: FIVE_MIN });
// Un informe por run_batch_tag no cambia una vez escrito (salvo re-ejecución
// manual del mismo tag): TTL largo.
export const getPortfolioReport = unstable_cache(q.getPortfolioReport, ["portfolio-report"], { revalidate: ONE_HOUR });
export const getPaperTradingReport = unstable_cache(q.getPaperTradingReport, ["paper-report"], { revalidate: ONE_HOUR });
export const getValidationReport = unstable_cache(q.getValidationReport, ["validation-report"], { revalidate: ONE_HOUR });
export const getLatestQualityScoreDate = unstable_cache(q.getLatestQualityScoreDate, ["latest-quality-date"], { revalidate: ONE_HOUR });
export const getQualityScores = unstable_cache(q.getQualityScores, ["quality-scores"], { revalidate: ONE_HOUR });
