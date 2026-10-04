// i18n.ts — la app en dos idiomas (español e inglés), sin dependencias.
//
// Cada texto se escribe junto a su traducción, en el sitio donde se usa:
// t("Señales", "Signals"). Con un fichero de claves aparte, la traducción y
// el contexto en que se lee viven lejos y se desincronizan; aquí, quien
// cambia una frase ve la otra en la misma línea.
//
// Este módulo no importa nada de Next: lo usan los componentes de servidor
// (vía lib/locale.ts) y los de cliente (vía LocaleProvider).

export type Locale = "es" | "en";

export const LOCALES: readonly Locale[] = ["es", "en"];
export const DEFAULT_LOCALE: Locale = "es";
export const LOCALE_COOKIE = "lang";

export type T = (es: string, en: string) => string;

export function makeT(locale: Locale): T {
  return (es, en) => (locale === "en" ? en : es);
}

export function normalizeLocale(value: string | null | undefined): Locale | null {
  return value === "es" || value === "en" ? value : null;
}

/** Primer idioma de Accept-Language que la app sabe hablar. Sin cabecera, o
 * con idiomas que no soporta, español: es el idioma original del producto. */
export function localeFromAcceptLanguage(header: string | null | undefined): Locale {
  if (!header) return DEFAULT_LOCALE;
  const langs = header
    .split(",")
    .map((part) => {
      const [tag, ...params] = part.trim().toLowerCase().split(";");
      const q = params.find((p) => p.trim().startsWith("q="));
      return { base: tag.split("-")[0], q: q ? Number(q.trim().slice(2)) || 0 : 1 };
    })
    .filter((l) => l.base)
    .sort((a, b) => b.q - a.q);
  for (const l of langs) {
    const found = normalizeLocale(l.base);
    if (found) return found;
  }
  return DEFAULT_LOCALE;
}

/** Etiqueta BCP 47 para Intl (fechas, números). */
export function intlTag(locale: Locale): string {
  return locale === "en" ? "en-US" : "es-ES";
}
