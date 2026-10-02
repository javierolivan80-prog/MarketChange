// labels.ts — un único sitio para el vocabulario visible del producto.
//
// Antes cada pantalla declaraba su propio mapa: la misma versión salía como
// "Equilibrado" en Cartera y "Balanceado" en Señales, y los tipos de evento
// se mostraban como el código interno del pipeline ("2.02_EARNINGS") en vez
// de en lenguaje llano. Un producto que habla de la misma cosa con dos
// nombres distintos transmite descuido — aquí se fija una sola palabra por
// concepto.
import type { StrategyVersion } from "./queries";

export const VERSION_ORDER: readonly StrategyVersion[] = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"];

export const VERSION_LABELS: Record<StrategyVersion, string> = {
  CONSERVATIVE: "Conservador",
  BALANCED: "Equilibrado",
  AGGRESSIVE: "Agresivo",
};

export function versionLabel(version: string): string {
  return VERSION_LABELS[version as StrategyVersion] ?? version;
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

/** Nombre legible de una clase de evento. Una clase nueva que el pipeline
 * añada sin pasar por aquí se muestra con su código (sin el prefijo 8K_) en
 * vez de desaparecer — mejor un código feo que un hueco. */
export function eventClassLabel(eventClass: string | null | undefined): string {
  if (!eventClass) return "—";
  return EVENT_CLASS_LABELS[eventClass] ?? eventClass.replace(/^8K_/, "");
}

const EXIT_REASON_LABELS: Record<string, string> = {
  TAKE_PROFIT: "Objetivo alcanzado",
  STOP_LOSS: "Stop loss",
  MAX_HOLDING: "Plazo máximo",
  TRAILING_STOP: "Stop dinámico",
};

export function exitReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "—";
  return EXIT_REASON_LABELS[reason] ?? reason;
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
