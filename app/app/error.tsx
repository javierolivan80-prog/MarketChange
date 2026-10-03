"use client";

// error.tsx — antes, si Postgres no respondía (Neon en frío, credenciales
// rotadas, límite de conexiones del plan gratuito), la página caía en el
// error genérico de Next.js en inglés. Ahora se explica qué ha pasado y se
// ofrece reintentar sin perder la navegación.
import { useEffect } from "react";
import { Nav } from "@/components/Nav";
import { Button } from "@/components/ui/Button";
import { useT } from "@/components/i18n/LocaleProvider";

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useT();
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto max-w-3xl px-4 py-4 sm:px-6">
      <Nav active="" />
      <h1 className="mb-4 text-2xl font-semibold tracking-tight text-foreground">{t("No se pudieron cargar los datos", "The data could not be loaded")}</h1>
      <div className="border border-border-subtle bg-surface p-4 text-sm text-text-secondary">
        <p className="mb-3">
          {t(
            "Lo más habitual es que la base de datos esté arrancando o no responda. Suele resolverse en unos segundos.",
            "Most often the database is starting up or not responding. It usually resolves within a few seconds.",
          )}
        </p>
        <Button variant="primary" onClick={reset}>
          {t("Reintentar", "Try again")}
        </Button>
        {error.digest && <p className="mt-3 font-mono text-xs text-text-tertiary">{t("Referencia", "Reference")}: {error.digest}</p>}
      </div>
    </main>
  );
}
