"use client";

// LanguageToggle.tsx — cambia entre español e inglés. Guarda la elección en
// una cookie (la lee el servidor, lib/locale.ts) y vuelve a pedir la página
// para que todo, también lo que pinta el servidor, salga en el idioma nuevo.
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { LOCALE_COOKIE, type Locale } from "@/lib/i18n";
import { useLocale, useT } from "@/components/i18n/LocaleProvider";

const OPTIONS: { value: Locale; label: string; name: string }[] = [
  { value: "es", label: "ES", name: "Español" },
  { value: "en", label: "EN", name: "English" },
];

export function LanguageToggle() {
  const locale = useLocale();
  const t = useT();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function choose(next: Locale) {
    if (next === locale) return;
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=31536000; samesite=lax`;
    startTransition(() => router.refresh());
  }

  return (
    // Control segmentado de Apple (Tanda 12c): pista translúcida y la opción
    // elegida como una píldora encima. Misma altura total que antes (36px, 44
    // con el dedo): la cuenta del sticky de la cabecera depende de ella. Con
    // el dedo la pista va sin margen para que cada opción mida 44px.
    <div role="group" aria-label={t("Idioma", "Language")} className={`flex rounded-full bg-fill p-0.5 text-xs pointer-coarse:p-0 ${pending ? "opacity-60" : ""}`}>
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          lang={o.value}
          onClick={() => choose(o.value)}
          aria-pressed={locale === o.value}
          title={o.name}
          className={`min-h-8 rounded-full px-2.5 font-medium transition-colors duration-(--motion-duration-micro) pointer-coarse:min-h-11 pointer-coarse:px-3 max-[359px]:px-2 ${
            locale === o.value
              ? "bg-surface text-foreground shadow-sm dark:bg-surface-pressed"
              : "text-text-secondary hover:text-foreground active:bg-fill-strong"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
