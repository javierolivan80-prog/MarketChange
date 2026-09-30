import type { Metadata } from "next";
import "./globals.css";
import { Disclaimer } from "@/components/ui/Disclaimer";

export const metadata: Metadata = {
  title: "MarketChange — Panel",
  description: "Señales event-driven: análisis Bull/Bear/Judge, event study por clase, backtest y memoria de tesis.",
};

// Script sin-flash: fija la clase .dark en <html> ANTES del primer pintado,
// a partir de la preferencia guardada o, si no hay ninguna, oscuro por
// defecto (no la preferencia del sistema) — pedido explícitamente: la app
// abre en oscuro como una terminal profesional, y solo pasa a claro si el
// usuario lo elige a mano con ThemeToggle.tsx. Sin este script la página
// arrancaría en claro y "saltaría" a oscuro tras hidratar — un parpadeo que
// en un producto que aspira a verse serio se nota de inmediato.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem("theme");
    var dark = stored !== "light";
    document.documentElement.classList.toggle("dark", dark);
  } catch (e) {}
})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="antialiased font-sans">
        <Disclaimer />
        {children}
      </body>
    </html>
  );
}
