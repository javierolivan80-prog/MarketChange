// Disclaimer.tsx — aviso legal sobrio y persistente, en todas las páginas.
// Antes iba ENCIMA de la navegación: en móvil ocupaba cuatro líneas de la
// primera pantalla, antes incluso del nombre del producto. Ahora es el pie
// de página — sigue en cada pantalla, legible, sin competir con el contenido.
export function Disclaimer() {
  return (
    <footer className="mx-auto mt-10 max-w-7xl border-t border-border-subtle px-4 py-4 text-center text-[11px] leading-snug text-text-tertiary sm:px-6">
      Contenido informativo generado por un sistema automatizado. No constituye asesoramiento de inversión ni una recomendación
      personalizada. Rendimiento pasado no garantiza resultados futuros.
    </footer>
  );
}
