// loading.tsx — todas las páginas con datos son force-dynamic y esperan a
// Postgres antes de pintar nada; sin esto, al cambiar de pestaña la pantalla
// se quedaba congelada en la anterior sin ninguna señal de que algo pasaba.
//
// Vive en el grupo (paneles) y no en la raíz a propósito: un loading.tsx
// raíz envuelve también /senales/[id], y con él la respuesta empieza a
// enviarse (con estado 200) antes de saber si la señal existe — una señal
// inexistente devolvía 200 en vez de 404.
//
// La barra de pestañas no se pinta aquí: está en el layout raíz y se queda
// quieta (y ya marcada) mientras llega la página.
import { getT } from "@/lib/locale";

export default async function Loading() {
  const { t } = await getT();
  return (
    <main className="mx-auto max-w-6xl px-4 pb-4 sm:px-6" aria-busy="true">
      <p className="sr-only">{t("Cargando…", "Loading…")}</p>
      {/* Misma forma que una página de datos (título, franja de cifras,
          tabla), para que al llegar el contenido no salte nada de sitio. El
          pulso es .m-skeleton: poca amplitud y nada con movimiento reducido. */}
      <div className="m-skeleton">
        {/* Sobre el fondo, bloques con el relleno translúcido (bg-fill); dentro
            de la tarjeta, con el de su superficie (Tanda 12c). */}
        <div className="mb-3 h-9 w-56 rounded-control bg-fill" />
        <div className="mb-6 h-4 w-80 max-w-full rounded-full bg-fill" />
        <div className="tarjeta mb-6 grid grid-cols-2 gap-x-6 gap-y-5 p-4 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i}>
              <div className="mb-2 h-3 w-20 rounded-full bg-surface-raised" />
              <div className="h-7 w-24 rounded-control bg-surface-raised" />
            </div>
          ))}
        </div>
        <div className="tarjeta overflow-hidden">
          <div className="h-9 border-b border-border-subtle" />
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="flex h-10 items-center gap-4 border-b border-border-subtle px-3 last:border-b-0">
              <div className="h-3 w-16 rounded-full bg-surface-raised" />
              <div className="h-3 flex-1 rounded-full bg-surface-raised" />
              <div className="h-3 w-12 rounded-full bg-surface-raised" />
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
