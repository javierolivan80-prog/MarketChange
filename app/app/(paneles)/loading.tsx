// loading.tsx — todas las páginas con datos son force-dynamic y esperan a
// Postgres antes de pintar nada; sin esto, al cambiar de pestaña la pantalla
// se quedaba congelada en la anterior sin ninguna señal de que algo pasaba.
//
// Vive en el grupo (paneles) y no en la raíz a propósito: un loading.tsx
// raíz envuelve también /senales/[id], y con él la respuesta empieza a
// enviarse (con estado 200) antes de saber si la señal existe — una señal
// inexistente devolvía 200 en vez de 404.
export default function Loading() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-4 sm:px-6" aria-busy="true">
      <p className="sr-only">Cargando…</p>
      <div className="mb-6 h-14 border-b border-border-subtle" />
      <div className="mb-3 h-7 w-48 animate-pulse bg-surface-raised motion-reduce:animate-none" />
      <div className="mb-6 h-4 w-80 max-w-full animate-pulse bg-surface-raised motion-reduce:animate-none" />
      <div className="h-64 animate-pulse border border-border-subtle bg-surface-raised motion-reduce:animate-none" />
    </main>
  );
}
