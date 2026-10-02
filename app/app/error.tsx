"use client";

// error.tsx — antes, si Postgres no respondía (Neon en frío, credenciales
// rotadas, límite de conexiones del plan gratuito), la página caía en el
// error genérico de Next.js en inglés. Ahora se explica qué ha pasado y se
// ofrece reintentar sin perder la navegación.
import { useEffect } from "react";
import { Nav } from "@/components/Nav";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto max-w-3xl px-4 py-4 sm:px-6">
      <Nav active="" />
      <h1 className="mb-4 text-2xl font-semibold tracking-tight text-foreground">No se pudieron cargar los datos</h1>
      <div className="border border-border-subtle bg-surface p-4 text-sm text-text-secondary">
        <p className="mb-3">
          Lo más habitual es que la base de datos esté arrancando o no responda. Suele resolverse en unos segundos.
        </p>
        <button
          type="button"
          onClick={reset}
          className="bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700"
        >
          Reintentar
        </button>
        {error.digest && <p className="mt-3 font-mono text-xs text-text-tertiary">Referencia: {error.digest}</p>}
      </div>
    </main>
  );
}
