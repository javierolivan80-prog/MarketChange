// PageState.tsx — pantallas de "no hay nada que enseñar todavía".
//
// Antes cada página tenía su propia copia (5 variantes, cada una con un tono
// distinto: una con recuadro ámbar y enlace al RUNBOOK, otras con una línea
// suelta "DATABASE_URL no está configurada."). Un estado vacío es lo primero
// que ve alguien nuevo — merece ser igual de cuidado en todas partes y decir
// siempre lo mismo: qué pasa, si es normal, y qué hacer.
import { Nav } from "@/components/Nav";

export function StatePage({ active, title, children }: { active: string; title: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto max-w-3xl px-4 py-4 sm:px-6">
      <Nav active={active} />
      <h1 className="mb-4 text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
      {children}
    </main>
  );
}

export function StateBox({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="border border-border-subtle bg-surface p-4">
      <p className="mb-1 font-medium text-foreground">{title}</p>
      {children && <div className="text-sm text-text-secondary">{children}</div>}
    </div>
  );
}

/** Falta DATABASE_URL: es un problema de despliegue, no del usuario — por eso
 * el mensaje no le habla de variables de entorno (ver RUNBOOK.md §4). */
export function NotConfigured({ active, title }: { active: string; title: string }) {
  return (
    <StatePage active={active} title={title}>
      <StateBox title="El servicio no está disponible en este momento.">
        Estamos trabajando en ello. Vuelve a intentarlo en unos minutos.
      </StateBox>
    </StatePage>
  );
}

/** El pipeline todavía no ha producido el dato que esta pantalla necesita. */
export function NoDataYet({ active, title, what }: { active: string; title: string; what: string }) {
  return (
    <StatePage active={active} title={title}>
      <StateBox title={what}>
        Los datos se actualizan varias veces al día. Vuelve a consultarlo más tarde.
      </StateBox>
    </StatePage>
  );
}
