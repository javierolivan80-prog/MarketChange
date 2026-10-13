"use client";

import { Button } from "@/components/ui/Button";
import { useT } from "@/components/i18n/LocaleProvider";

// ThemeToggle.tsx — selector manual claro/oscuro persistente (localStorage).
// La app abre en oscuro (decisión del usuario, ver layout.tsx) y pasa a claro
// solo si se elige aquí. El script sin-flash que fija la clase ANTES del
// primer pintado vive en layout.tsx.
//
// El botón dice la ACCIÓN ("Modo claro"), no el estado actual. La etiqueta
// la decide el CSS (dark:) en vez de un estado de React: antes el servidor
// pintaba "Modo oscuro", y tras hidratar cambiaba a "Modo claro" (un
// parpadeo en cada carga que además movía la cabecera).
//
// El cambio de tema se funde con una transición de vista cuando el
// navegador la tiene y el usuario no pide menos movimiento, en vez de saltar
// de golpe de negro a blanco (Tanda 12).

function aplicarTema(oscuro: boolean) {
  document.documentElement.classList.toggle("dark", oscuro);
  try {
    localStorage.setItem("theme", oscuro ? "dark" : "light");
  } catch {
    // localStorage puede fallar (modo privado); no es crítico para la función.
  }
}

export function ThemeToggle() {
  const t = useT();

  function toggle() {
    const oscuro = !document.documentElement.classList.contains("dark");
    const reducir = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
    if (!reducir && typeof doc.startViewTransition === "function") {
      doc.startViewTransition(() => aplicarTema(oscuro));
    } else {
      aplicarTema(oscuro);
    }
  }

  return (
    // Por debajo de 360px "Modo " se oculta a la vista (no al lector de
    // pantalla, que sigue leyendo "Modo oscuro"): la fila del logo no cabía.
    <Button variant="ghost" onClick={toggle} className="whitespace-nowrap text-xs max-[359px]:px-2">
      <span className="dark:hidden">
        <span className="max-[359px]:sr-only">{t("Modo ", "")}</span>
        <span className="max-[359px]:capitalize">{t("oscuro", "Dark mode")}</span>
      </span>
      <span className="hidden dark:inline">
        <span className="max-[359px]:sr-only">{t("Modo ", "")}</span>
        <span className="max-[359px]:capitalize">{t("claro", "Light mode")}</span>
      </span>
    </Button>
  );
}
