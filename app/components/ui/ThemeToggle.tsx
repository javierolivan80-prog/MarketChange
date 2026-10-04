"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { useT } from "@/components/i18n/LocaleProvider";

// ThemeToggle.tsx — selector manual claro/oscuro persistente (localStorage),
// en vez de depender solo de prefers-color-scheme: en un puesto de trabajo,
// el usuario decide su tema con independencia del SO (mismo patrón que
// cualquier terminal profesional). El script sin-flash que fija la clase
// ANTES del primer pintado vive en layout.tsx.
//
// El botón dice la ACCIÓN ("Modo claro"), no el estado actual: antes ponía
// "Oscuro" estando ya en oscuro, y no quedaba claro si era lo que había o lo
// que iba a pasar.

export function ThemeToggle() {
  const [isDark, setIsDark] = useState(false);
  const t = useT();

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      // localStorage puede fallar (modo privado); no es crítico para la función.
    }
  }

  return (
    <Button variant="ghost" onClick={toggle} aria-label={isDark ? t("Cambiar a modo claro", "Switch to light mode") : t("Cambiar a modo oscuro", "Switch to dark mode")}
      className="text-xs"
    >
      {isDark ? t("Modo claro", "Light mode") : t("Modo oscuro", "Dark mode")}
    </Button>
  );
}
