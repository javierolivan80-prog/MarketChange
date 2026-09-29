// Disclaimer.tsx — aviso legal sobrio y persistente. Ni un modal que haya
// que cerrar, ni letra ilegible al pie: una línea fija, siempre visible,
// exactamente donde ya se mira (bajo la navegación), en el mismo tono
// sereno que el resto de la interfaz — no es una advertencia de urgencia,
// es una precisión de qué es este producto.
export function Disclaimer() {
  return (
    <p className="border-b border-border-subtle bg-surface-raised px-6 py-1.5 text-center text-[11px] leading-tight text-text-tertiary">
      Contenido informativo generado por un sistema automatizado. No constituye asesoramiento de inversión ni una recomendación
      personalizada. Rendimiento pasado no garantiza resultados futuros.
    </p>
  );
}
