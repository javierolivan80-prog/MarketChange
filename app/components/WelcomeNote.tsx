"use client";

// WelcomeNote.tsx — onboarding mínimo: la primera visita responde en tres
// líneas qué es esto, qué se ve y cómo se lee, y se puede cerrar para
// siempre. Sustituye a tener que descubrir "Cómo funciona" por cuenta propia.
// Se recuerda en localStorage (por navegador): si no está disponible, la
// nota simplemente vuelve a salir, sin romper nada.
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";

const KEY = "welcome-dismissed-v1";

export function WelcomeNote() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      setVisible(localStorage.getItem(KEY) !== "1");
    } catch {
      setVisible(true);
    }
  }, []);

  function dismiss() {
    setVisible(false);
    try {
      localStorage.setItem(KEY, "1");
    } catch {
      // modo privado: no se recuerda, no es crítico
    }
  }

  if (!visible) return null;

  return (
    <aside aria-label="Qué es MarketChange" className="mb-5 border border-accent-500/50 bg-surface p-4 text-sm">
      <p className="mb-1 font-medium text-foreground">Qué es esto</p>
      <p className="mb-2 text-text-secondary">
        Cada noche, MarketChange lee los hechos relevantes que las empresas cotizadas de EE. UU. presentan ante la SEC y las decisiones de
        la FDA. Para cada uno decide si el mercado ya lo sabía, debate a favor y en contra con IA y solo señala los que tienen un valor
        esperado positivo después de costes. La mayoría se descartan, y cada descarte dice por qué.
      </p>
      <div className="flex flex-wrap items-center gap-4">
        <Link href="/como-funciona" className="text-accent-700 hover:underline dark:text-accent-400">
          Cómo funciona →
        </Link>
        <Button variant="ghost" onClick={dismiss}>
          Entendido, no volver a mostrar
        </Button>
      </div>
    </aside>
  );
}
