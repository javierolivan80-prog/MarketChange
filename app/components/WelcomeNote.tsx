"use client";

// WelcomeNote.tsx — onboarding mínimo: la primera visita responde en tres
// líneas qué es esto, qué se ve y cómo se lee, y se puede cerrar para
// siempre. Sustituye a tener que descubrir "Cómo funciona" por cuenta propia.
// Se recuerda en localStorage (por navegador): si no está disponible, la
// nota simplemente vuelve a salir, sin romper nada.
//
// Se pinta ya en el servidor (si estaba cerrada, la oculta el script de
// layout.tsx antes del primer pintado) para no desplazar la página al
// hidratar. Al cerrarla se desvanece (ease-in) y después se desmonta; con
// movimiento reducido desaparece en el acto.
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { useT } from "@/components/i18n/LocaleProvider";

const KEY = "welcome-dismissed-v1";

export function WelcomeNote() {
  const [visible, setVisible] = useState(true);
  const [leaving, setLeaving] = useState(false);
  const t = useT();

  useEffect(() => {
    try {
      if (localStorage.getItem(KEY) === "1") setVisible(false);
    } catch {
      // sin localStorage: se queda visible
    }
  }, []);

  function dismiss() {
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced) setVisible(false);
    else setLeaving(true);
    try {
      localStorage.setItem(KEY, "1");
    } catch {
      // modo privado: no se recuerda, no es crítico
    }
  }

  if (!visible) return null;

  return (
    <aside
      data-welcome-note
      aria-label={t("Qué es MarketChange", "What MarketChange is")}
      className={`mb-5 border border-accent-500/50 bg-surface p-4 text-sm ${leaving ? "m-exit" : "m-fade"}`}
      onAnimationEnd={(e) => {
        if (leaving && e.target === e.currentTarget) setVisible(false);
      }}
    >
      <p className="mb-1 font-medium text-foreground">{t("Qué es esto", "What this is")}</p>
      <p className="mb-2 text-text-secondary">
        {t(
          "Cada noche, MarketChange lee los hechos relevantes que las empresas cotizadas de EE. UU. presentan ante la SEC y las decisiones de la FDA. Para cada uno decide si el mercado ya lo sabía, debate a favor y en contra con IA y solo señala los que tienen un valor esperado positivo después de costes. La mayoría se descartan, y cada descarte dice por qué.",
          "Every night, MarketChange reads the material events that US-listed companies file with the SEC, and FDA decisions. For each one it decides whether the market already knew, runs an AI debate for and against, and only flags those with a positive expected value after costs. Most are discarded, and each discard says why.",
        )}
      </p>
      <div className="flex flex-wrap items-center gap-4">
        <Link href="/como-funciona" className="text-accent-700 hover:underline dark:text-accent-400">
          {t("Cómo funciona →", "How it works →")}
        </Link>
        <Button variant="ghost" onClick={dismiss}>
          {t("Entendido, no volver a mostrar", "Got it, don't show again")}
        </Button>
      </div>
    </aside>
  );
}
