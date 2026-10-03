"use client";

// LocaleProvider.tsx — el idioma, disponible en los componentes de cliente.
// Lo fija el layout raíz con el idioma que ya resolvió el servidor
// (lib/locale.ts), así servidor y cliente pintan siempre el mismo texto.
import { createContext, useContext, useMemo } from "react";
import { makeT, type Locale, type T } from "@/lib/i18n";

const LocaleContext = createContext<Locale>("es");

export function LocaleProvider({ locale, children }: { locale: Locale; children: React.ReactNode }) {
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

export function useLocale(): Locale {
  return useContext(LocaleContext);
}

export function useT(): T {
  const locale = useLocale();
  return useMemo(() => makeT(locale), [locale]);
}
