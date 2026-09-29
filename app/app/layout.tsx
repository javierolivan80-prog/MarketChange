import type { Metadata } from "next";
import "./globals.css";
import { Disclaimer } from "@/components/ui/Disclaimer";

export const metadata: Metadata = {
  title: "MarketChange — Panel",
  description: "Señales event-driven: análisis Bull/Bear/Judge, event study por clase, backtest y memoria de tesis.",
};

// Script sin-flash: fija la clase .dark en <html> ANTES del primer pintado,
// a partir de la preferencia guardada o, si no hay ninguna, del sistema.
// Sin esto, con dark mode manual (ver ThemeToggle.tsx) la página siempre
// arrancaría en claro y "saltaría" a oscuro tras hidratar — un parpadeo que
// en un producto que aspira a verse serio se nota de inmediato.
const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem("theme");
    var dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
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
