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
    <div role="group" aria-label={t("Idioma", "Language")} className={`flex text-xs ${pending ? "opacity-60" : ""}`}>
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          lang={o.value}
          onClick={() => choose(o.value)}
          aria-pressed={locale === o.value}
          title={o.name}
          className={`px-2 py-1 font-mono ${
            locale === o.value ? "font-semibold text-foreground" : "text-text-tertiary hover:text-foreground"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
