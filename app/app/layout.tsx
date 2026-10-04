import type { Metadata } from "next";
import "./globals.css";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { LocaleProvider } from "@/components/i18n/LocaleProvider";
import { getLocale, getT } from "@/lib/locale";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return {
    title: { default: "MarketChange", template: "%s · MarketChange" },
    description: t(
      "Señales sobre filings de la SEC y decisiones de la FDA: qué ha pasado, si el mercado ya lo sabía y si merece la pena operarlo.",
      "Signals from SEC filings and FDA decisions: what happened, whether the market already knew, and whether it is worth trading.",
    ),
    // Panel privado de un sistema en validación: no tiene sentido que lo indexen.
    robots: { index: false, follow: false },
  };
}

// Script sin-flash: fija la clase .dark en <html> ANTES del primer pintado,
// a partir de la preferencia guardada o, si no hay ninguna, oscuro por
// defecto (no la preferencia del sistema) — pedido explícitamente: la app
// abre en oscuro como una terminal profesional, y solo pasa a claro si el
// usuario lo elige a mano con ThemeToggle.tsx. Sin este script la página
// arrancaría en claro y "saltaría" a oscuro tras hidratar — un parpadeo que
// en un producto que aspira a verse serio se nota de inmediato.
//
// Por lo mismo marca si la nota de bienvenida ya se cerró (misma clave que
// WelcomeNote.tsx): la nota se pinta en el servidor y globals.css la oculta
// antes del primer pintado, en vez de aparecer tras hidratar y empujar la
// página hacia abajo.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem("theme");
    var dark = stored !== "light";
    document.documentElement.classList.toggle("dark", dark);
    if (localStorage.getItem("welcome-dismissed-v1") === "1") document.documentElement.dataset.welcomeDismissed = "1";
  } catch (e) {}
})();
`;

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const locale = await getLocale();
  return (
    <html lang={locale} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="antialiased font-sans">
        <LocaleProvider locale={locale}>
          {children}
          <Disclaimer />
        </LocaleProvider>
      </body>
    </html>
  );
}
