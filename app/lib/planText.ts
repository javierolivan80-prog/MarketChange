// planText.ts — traduce al inglés los textos del plan técnico.
//
// El plan lo escribe pipeline/analyze/technical_analysis.py en español
// (niveles, indicadores, avisos, reglas de salida, motivo de rechazo). Son
// un conjunto cerrado de frases, así que se traducen aquí con frases exactas
// y patrones para las que llevan cifras, sin tocar el pipeline ni los datos
// ya guardados. Una frase que no se reconoce se enseña tal cual: mejor en
// español que inventada.
import type { Locale } from "./i18n";

const EXACT: Record<string, string> = {
  // Indicadores alineados
  "Medias móviles": "Moving averages",
  "Cruce dorado (SMA 50/200)": "Golden cross (SMA 50/200)",
  "Cruce de la muerte (SMA 50/200)": "Death cross (SMA 50/200)",
  "Ruptura con volumen": "Breakout on volume",
  // Niveles
  "Bollinger superior": "Upper Bollinger band",
  "Bollinger inferior": "Lower Bollinger band",
  "Banda ATR superior": "Upper ATR band",
  "Banda ATR inferior": "Lower ATR band",
  "Volume profile: área de valor baja": "Volume profile: value area low",
  "Volume profile: área de valor alta": "Volume profile: value area high",
  "3 ATR (sin resistencia por encima)": "3 ATR (no resistance above)",
  "3 ATR (sin soporte por debajo)": "3 ATR (no support below)",
  // Límites del análisis
  "Solo precios diarios: sin niveles ni divergencias en 4H/1H.": "Daily prices only: no 4H/1H levels or divergences.",
  "Sin volatilidad implícita del subyacente ni posicionamiento en opciones (el VIX sí se usa).":
    "No implied volatility for the stock or options positioning (the VIX is used).",
  "Sin sentimiento en redes, flujo institucional, operaciones de insiders ni order flow.":
    "No social sentiment, institutional flow, insider trades or order flow.",
  "Sin calendario de anuncios: todas las señales son posteriores al evento (entrada en D+1).":
    "No announcement calendar: every signal comes after the event (entry on D+1).",
  "VWAP de 20 sesiones y volume profile sobre precio típico diario, no intradía.":
    "20-session VWAP and volume profile on the daily typical price, not intraday.",
  // Reglas de salida
  "Entrada escalonada: 50% en la apertura siguiente al evento, 50% si cierra a favor el primer día.":
    "Scaled entry: 50% at the open after the event, 50% if the first day closes in favour.",
  "Tomar la mitad en el objetivo parcial y subir el stop al precio de entrada.":
    "Take half at the partial target and move the stop to the entry price.",
  "Salida forzada si aparece una divergencia contraria en el RSI diario o se pierde el soporte del stop.":
    "Forced exit if an opposing divergence appears on the daily RSI or the stop's support is lost.",
  // Motivos de rechazo (piezas, ver translateReason)
  "catalizador no confirmado": "catalyst not confirmed",
  "ningún indicador alineado": "no aligned indicators",
  "solo 1 indicador alineado": "only 1 aligned indicator",
  "sin soporte real para el stop": "no real support for the stop",
  "sin resistencia real para el stop": "no real resistance for the stop",
  "Sin volatilidad medible (ATR nulo)": "No measurable volatility (zero ATR)",
};

const PATTERNS: [RegExp, (m: RegExpMatchArray) => string][] = [
  [/^Mínimo (\d+) sesiones$/, (m) => `${m[1]}-session low`],
  [/^Máximo (\d+) sesiones$/, (m) => `${m[1]}-session high`],
  [/^([\d.]+) ATR \(sin soporte cercano\)$/, (m) => `${m[1]} ATR (no nearby support)`],
  [/^Con la otra mitad, stop dinámico a ([\d.]+) ATR del extremo alcanzado\.$/, (m) => `With the other half, a trailing stop ${m[1]} ATR from the extreme reached.`],
  [/^VIX en (\d+): mercado en tensión, tamaño a la mitad\.$/, (m) => `VIX at ${m[1]}: market under stress, position size halved.`],
  [/^VIX en (\d+): volatilidad de mercado elevada, tamaño reducido un 25%\.$/, (m) => `VIX at ${m[1]}: elevated market volatility, position size cut by 25%.`],
  [
    /^El objetivo pide un ([\d.]+)%, más que el movimiento típico de eventos parecidos \(±([\d.]+)%\): conviene tomar beneficios antes\.$/,
    (m) => `The target needs a ${m[1]}% move, more than the typical move for similar events (±${m[2]}%): consider taking profits earlier.`,
  ],
  // Línea de impacto (pipeline/analyze/historical_analogues.py), junto a las cifras del análisis.
  [/^±([\d.]+)% según histórico de (\d+) eventos análogos$/, (m) => `±${m[1]}% based on ${m[2]} similar past events`],
  [/^solo (\d+) indicadores alineados$/, (m) => `only ${m[1]} aligned indicators`],
  [/^riesgo\/beneficio (.+) < (.+)$/, (m) => `risk/reward ${m[1]} < ${m[2]}`],
  [/^Histórico insuficiente \((\d+) sesiones; hacen falta (\d+)\)$/, (m) => `Not enough history (${m[1]} sessions; ${m[2]} needed)`],
];

function translateOne(text: string): string {
  if (EXACT[text]) return EXACT[text];
  for (const [re, fn] of PATTERNS) {
    const m = text.match(re);
    if (m) return fn(m);
  }
  return text;
}

/** Una frase del plan (nivel, indicador, aviso, regla, límite) en el idioma pedido. */
export function planText(text: string, locale: Locale): string {
  return locale === "en" ? translateOne(text) : text;
}

/** El motivo de rechazo son varias piezas unidas con "; ", la primera en
 * mayúscula y un punto final ("Catalizador no confirmado; solo 1 indicador
 * alineado."). Se traduce pieza a pieza y se vuelve a montar igual. */
export function planReason(text: string, locale: Locale): string {
  if (locale !== "en") return text;
  const body = text.endsWith(".") ? text.slice(0, -1) : text;
  // Motivos de una sola pieza que llevan "; " dentro (el de histórico insuficiente).
  if (translateOne(body) !== body) return translateOne(body) + ".";
  const parts = body.split("; ").map((p, i) => {
    const plain = i === 0 ? p.charAt(0).toLowerCase() + p.slice(1) : p;
    return translateOne(plain) !== plain ? translateOne(plain) : translateOne(p);
  });
  const joined = parts.join("; ");
  return joined.charAt(0).toUpperCase() + joined.slice(1) + ".";
}
