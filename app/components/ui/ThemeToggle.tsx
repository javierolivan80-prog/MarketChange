"use client";

import { useEffect, useState } from "react";

// ThemeToggle.tsx — selector manual claro/oscuro persistente (localStorage),
// en vez de depender solo de prefers-color-scheme: en un puesto de trabajo,
// el usuario decide su tema con independencia del SO (mismo patrón que
// cualquier terminal profesional). El script sin-flash que fija la clase
// ANTES del primer pintado vive en layout.tsx.

export function ThemeToggle() {
  const [isDark, setIsDark] = useState(false);

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
    <button
      onClick={toggle}
      aria-label={isDark ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
      aria-pressed={isDark}
      className="rounded border border-border-subtle px-2 py-1.5 text-xs text-text-secondary hover:border-border-strong hover:text-foreground"
    >
      {isDark ? "Oscuro" : "Claro"}
    </button>
  );
}
