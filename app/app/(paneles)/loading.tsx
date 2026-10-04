// loading.tsx — todas las páginas con datos son force-dynamic y esperan a
// Postgres antes de pintar nada; sin esto, al cambiar de pestaña la pantalla
// se quedaba congelada en la anterior sin ninguna señal de que algo pasaba.
//
// Vive en el grupo (paneles) y no en la raíz a propósito: un loading.tsx
// raíz envuelve también /senales/[id], y con él la respuesta empieza a
// enviarse (con estado 200) antes de saber si la señal existe — una señal
// inexistente devolvía 200 en vez de 404.
import { getT } from "@/lib/locale";

export default async function Loading() {
  const { t } = await getT();
  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6" aria-busy="true">
      <p className="sr-only">{t("Cargando…", "Loading…")}</p>
      <div className="mb-6 h-14 border-b border-border-subtle" />
      {/* Misma forma que una página de datos (título, franja de cifras,
          tabla), para que al llegar el contenido no salte nada de sitio. El
          pulso es .m-skeleton: poca amplitud y nada con movimiento reducido. */}
      <div className="m-skeleton">
        <div className="mb-3 h-7 w-48 bg-surface-raised" />
        <div className="mb-6 h-4 w-80 max-w-full bg-surface-raised" />
        <div className="mb-6 flex flex-wrap gap-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="min-w-[140px] flex-1 border-l border-border-subtle pl-3">
              <div className="mb-2 h-3 w-20 bg-surface-raised" />
              <div className="h-7 w-24 bg-surface-raised" />
            </div>
          ))}
        </div>
        <div className="border border-border-subtle">
          <div className="h-9 border-b border-border-strong bg-surface-raised" />
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="flex h-10 items-center gap-4 border-b border-border-subtle px-3 last:border-b-0">
              <div className="h-3 w-16 bg-surface-raised" />
              <div className="h-3 flex-1 bg-surface-raised" />
              <div className="h-3 w-12 bg-surface-raised" />
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
