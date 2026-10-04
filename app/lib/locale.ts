// locale.ts — idioma de la petición actual, para componentes de servidor.
//
// Orden: la cookie `lang` (elegida con el selector de idioma) y, si no hay,
// el Accept-Language del navegador. Leer cookies/headers hace la página
// dinámica, y lo son todas ya (force-dynamic); los datos siguen cacheados
// en lib/data.ts, que no depende del idioma.
import { cookies, headers } from "next/headers";
import { LOCALE_COOKIE, localeFromAcceptLanguage, makeT, normalizeLocale, type Locale, type T } from "./i18n";

export async function getLocale(): Promise<Locale> {
  const fromCookie = normalizeLocale((await cookies()).get(LOCALE_COOKIE)?.value);
  if (fromCookie) return fromCookie;
  return localeFromAcceptLanguage((await headers()).get("accept-language"));
}

export async function getT(): Promise<{ locale: Locale; t: T }> {
  const locale = await getLocale();
  return { locale, t: makeT(locale) };
}
