// qualityText.ts — traduce al inglés los textos del ranking de largo plazo.
//
// Los escribe pipeline/analyze/quality_score.py en español (nombre de cada
// criterio, explicación con su cifra y veredicto). Mismo enfoque que
// planText.ts: frases exactas y patrones para las que llevan números; lo que
// no se reconoce se enseña tal cual. Las explicaciones son ratios "por cada
// 100 de capital" y el pipeline las escribe con €; en inglés van con $,
// que es la moneda en que informan estas empresas.
import type { Locale } from "./i18n";

const EXACT: Record<string, string> = {
  Rentabilidad: "Profitability",
  "Solidez financiera": "Financial strength",
  "Calidad del beneficio": "Earnings quality",
  Crecimiento: "Growth",
  Precio: "Price",
  "No se puede calcular: faltan beneficio neto o fondos propios (o los fondos propios son negativos).":
    "Cannot be calculated: net income or equity is missing (or equity is negative).",
  "No se puede calcular: no hay fondos propios utilizables.": "Cannot be calculated: no usable equity.",
  "No se puede calcular: falta el flujo de caja operativo, el capex, o la empresa no tuvo beneficio.":
    "Cannot be calculated: operating cash flow or capex is missing, or the company had no profit.",
  "El beneficio contable no se convierte en caja: el flujo de caja libre es negativo.":
    "Accounting profit does not turn into cash: free cash flow is negative.",
  "No se puede calcular: falta el precio de la acción o el número de acciones.":
    "Cannot be calculated: the share price or share count is missing.",
  "No se puede calcular un PER: la empresa no tuvo beneficio en el último ejercicio disponible.":
    "A P/E cannot be calculated: the company had no profit in the latest fiscal year available.",
  "Sin datos suficientes para valorar esta empresa.": "Not enough data to assess this company.",
  "Datos incompletos — esta nota se apoya en muy pocos criterios, tómala con reservas.":
    "Incomplete data — this score rests on very few criteria; take it with caution.",
  "Negocio sólido a un precio razonable según sus propias cuentas.": "A solid business at a reasonable price, based on its own accounts.",
  "Negocio decente, con algún punto flojo — mira el desglose.": "A decent business with some weak spots — see the breakdown.",
  "Tiene problemas visibles en varios criterios.": "Visible problems across several criteria.",
  "Flojo en casi todos los criterios.": "Weak on almost every criterion.",
};

const N = "([\\d.,-]+)";
const PATTERNS: [RegExp, (m: RegExpMatchArray) => string][] = [
  [new RegExp(`^Excelente: gana ${N}€ al año por cada 100€ de capital propio\\.$`), (m) => `Excellent: earns $${m[1]} a year for every $100 of equity.`],
  [new RegExp(`^Buena: gana ${N}€ al año por cada 100€ de capital propio\\.$`), (m) => `Good: earns $${m[1]} a year for every $100 of equity.`],
  [new RegExp(`^Floja: solo gana ${N}€ al año por cada 100€ de capital propio\\.$`), (m) => `Weak: earns only $${m[1]} a year for every $100 of equity.`],
  [new RegExp(`^Pierde dinero: ${N}% sobre el capital propio\\.$`), (m) => `Loses money: ${m[1]}% on equity.`],
  [new RegExp(`^Sólida: debe ${N}€ por cada euro de capital propio — poco apalancada\\.$`), (m) => `Solid: owes $${m[1]} for every dollar of equity — low leverage.`],
  [new RegExp(`^Aceptable: debe ${N}€ por cada euro de capital propio\\.$`), (m) => `Acceptable: owes $${m[1]} for every dollar of equity.`],
  [new RegExp(`^Muy endeudada: debe ${N}€ por cada euro de capital propio\\.$`), (m) => `Heavily indebted: owes $${m[1]} for every dollar of equity.`],
  [
    new RegExp(`^El beneficio es caja real: por cada 100€ de beneficio contable entran ${N}€ de caja libre\\.$`),
    (m) => `Profit is real cash: every $100 of accounting profit brings in $${m[1]} of free cash flow.`,
  ],
  [
    new RegExp(`^Ojo: por cada 100€ de beneficio contable solo entran ${N}€ de caja libre\\.$`),
    (m) => `Careful: every $100 of accounting profit brings in only $${m[1]} of free cash flow.`,
  ],
  [
    /^No se puede calcular: hacen falta al menos (\d+) ejercicios con ingresos y solo hay (\d+)\.$/,
    (m) => `Cannot be calculated: at least ${m[1]} fiscal years with revenue are needed and there are only ${m[2]}.`,
  ],
  [
    new RegExp(`^Crece con fuerza: los ingresos suben un ${N}% al año de media en los últimos (\\d+) ejercicios\\.$`),
    (m) => `Strong growth: revenue rises ${m[1]}% a year on average over the last ${m[2]} fiscal years.`,
  ],
  [new RegExp(`^Crece despacio: los ingresos suben un ${N}% al año de media\\.$`), (m) => `Slow growth: revenue rises ${m[1]}% a year on average.`],
  [new RegExp(`^Se encoge: los ingresos bajan un ${N}% al año de media\\.$`), (m) => `Shrinking: revenue falls ${m[1]}% a year on average.`],
  [new RegExp(`^Barata: se paga ${N}€ por cada euro de beneficio anual\\.$`), (m) => `Cheap: you pay $${m[1]} for every dollar of annual earnings.`],
  [new RegExp(`^Precio razonable: se paga ${N}€ por cada euro de beneficio anual\\.$`), (m) => `Reasonable price: you pay $${m[1]} for every dollar of annual earnings.`],
  [new RegExp(`^Cara: se paga ${N}€ por cada euro de beneficio anual\\.$`), (m) => `Expensive: you pay $${m[1]} for every dollar of annual earnings.`],
  [
    new RegExp(`^Muy cara: se paga ${N}€ por cada euro de beneficio anual — el mercado descuenta mucho crecimiento futuro\\.$`),
    (m) => `Very expensive: you pay $${m[1]} for every dollar of annual earnings — the market is pricing in a lot of future growth.`,
  ],
];

export function qualityText(text: string, locale: Locale): string {
  if (locale !== "en") return text;
  if (EXACT[text]) return EXACT[text];
  for (const [re, fn] of PATTERNS) {
    const m = text.match(re);
    if (m) return fn(m);
  }
  return text;
}
