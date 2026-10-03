// labels.ts — un único sitio para el vocabulario visible del producto.
//
// Antes cada pantalla declaraba su propio mapa: la misma versión salía como
// "Equilibrado" en Cartera y "Balanceado" en Señales, y los tipos de evento
// se mostraban como el código interno del pipeline ("2.02_EARNINGS") en vez
// de en lenguaje llano. Un producto que habla de la misma cosa con dos
// nombres distintos transmite descuido — aquí se fija una sola palabra por
// concepto.
//
// Cada etiqueta existe en español e inglés (lib/i18n.ts); el idioma es el
// último argumento y por defecto español.
import type { Locale } from "./i18n";
import type { StrategyVersion } from "./queries";

export const VERSION_ORDER: readonly StrategyVersion[] = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"];

export const VERSION_LABELS: Record<StrategyVersion, string> = {
  CONSERVATIVE: "Conservador",
  BALANCED: "Equilibrado",
  AGGRESSIVE: "Agresivo",
};

const VERSION_LABELS_EN: Record<StrategyVersion, string> = {
  CONSERVATIVE: "Conservative",
  BALANCED: "Balanced",
  AGGRESSIVE: "Aggressive",
};

export function versionLabels(locale: Locale = "es"): Record<StrategyVersion, string> {
  return locale === "en" ? VERSION_LABELS_EN : VERSION_LABELS;
}

export function versionLabel(version: string, locale: Locale = "es"): string {
  return versionLabels(locale)[version as StrategyVersion] ?? version;
}

// Claves = event_class tal como lo escribe el pipeline
// (pipeline/ingest/edgar_scraper.py:ITEM_TO_EVENT_CLASS y las clases FDA).
const EVENT_CLASS_LABELS: Record<string, string> = {
  "8K_2.02_EARNINGS": "Resultados",
  "8K_1.01_MATERIAL_AGMT": "Acuerdo relevante / M&A",
  "8K_4.02_RESTATEMENT": "Reformulación de cuentas",
  "8K_5.02_MGMT_CHANGE": "Cambio en la dirección",
  "8K_1.03_BANKRUPTCY": "Concurso / quiebra",
  "8K_4.01_AUDITOR_CHANGE": "Cambio de auditor",
  "8K_8.01_OTHER": "Otros hechos relevantes",
  FDA_APPROVAL: "Aprobación FDA",
  FDA_CRL: "Rechazo FDA (CRL)",
};

const EVENT_CLASS_LABELS_EN: Record<string, string> = {
  "8K_2.02_EARNINGS": "Earnings",
  "8K_1.01_MATERIAL_AGMT": "Material agreement / M&A",
  "8K_4.02_RESTATEMENT": "Financial restatement",
  "8K_5.02_MGMT_CHANGE": "Management change",
  "8K_1.03_BANKRUPTCY": "Bankruptcy",
  "8K_4.01_AUDITOR_CHANGE": "Auditor change",
  "8K_8.01_OTHER": "Other material events",
  FDA_APPROVAL: "FDA approval",
  FDA_CRL: "FDA rejection (CRL)",
};

/** Nombre legible de una clase de evento. Una clase nueva que el pipeline
 * añada sin pasar por aquí se muestra con su código (sin el prefijo 8K_) en
 * vez de desaparecer — mejor un código feo que un hueco. */
export function eventClassLabel(eventClass: string | null | undefined, locale: Locale = "es"): string {
  if (!eventClass) return "—";
  const labels = locale === "en" ? EVENT_CLASS_LABELS_EN : EVENT_CLASS_LABELS;
  return labels[eventClass] ?? eventClass.replace(/^8K_/, "");
}

const EXIT_REASON_LABELS: Record<string, string> = {
  TAKE_PROFIT: "Objetivo alcanzado",
  STOP_LOSS: "Stop loss",
  MAX_HOLDING: "Plazo máximo",
  TRAILING_STOP: "Stop dinámico",
};

const EXIT_REASON_LABELS_EN: Record<string, string> = {
  TAKE_PROFIT: "Target reached",
  STOP_LOSS: "Stop loss",
  MAX_HOLDING: "Maximum holding period",
  TRAILING_STOP: "Trailing stop",
};

export function exitReasonLabel(reason: string | null | undefined, locale: Locale = "es"): string {
  if (!reason) return "—";
  return (locale === "en" ? EXIT_REASON_LABELS_EN : EXIT_REASON_LABELS)[reason] ?? reason;
}

const SOURCE_LABELS: Record<string, string> = { EDGAR: "SEC", FDA_OPENFDA: "FDA" };

export function sourceLabel(source: string | null | undefined): string {
  if (!source) return "—";
  return SOURCE_LABELS[source] ?? source;
}

// Veredicto del motor de validación (pipeline/validation/decision.py, opción
// A/B/C) contado al usuario como una calificación de fiabilidad. El motor
// trae además una "recommendation" pensada para el operador del sistema
// ("Paper trade 3 meses más antes de live", "Invierte capital mínimo…"): eso
// no se enseña a los usuarios — leído por un consumidor sería una
// recomendación de inversión, y está en jerga interna.
export const RELIABILITY: Record<"A" | "B" | "C", { title: string; summary: string }> = {
  A: {
    title: "Fiabilidad alta",
    summary: "Los resultados se sostienen en todas las comprobaciones: significación estadística, calibración y escenarios adversos.",
  },
  B: {
    title: "Fiabilidad media",
    summary: "Los resultados son prometedores, pero alguna comprobación todavía no es concluyente. Conviene seguirlos con cautela.",
  },
  C: {
    title: "Fiabilidad baja",
    summary: "Con los datos disponibles los resultados todavía no son concluyentes. Tómalos como orientativos.",
  },
};

const RELIABILITY_EN: Record<"A" | "B" | "C", { title: string; summary: string }> = {
  A: {
    title: "High reliability",
    summary: "The results hold up in every check: statistical significance, calibration and adverse scenarios.",
  },
  B: {
    title: "Medium reliability",
    summary: "The results are promising, but some checks are not yet conclusive. Follow them with caution.",
  },
  C: {
    title: "Low reliability",
    summary: "With the data available, the results are not yet conclusive. Treat them as indicative only.",
  },
};

export function reliability(locale: Locale = "es"): Record<"A" | "B" | "C", { title: string; summary: string }> {
  return locale === "en" ? RELIABILITY_EN : RELIABILITY;
}
