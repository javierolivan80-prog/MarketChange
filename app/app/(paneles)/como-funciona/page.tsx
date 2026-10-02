import { Nav } from "@/components/Nav";

export const metadata = { title: "Cómo funciona" };

// como-funciona/page.tsx — página nueva, sin datos: explica el motor paso a
// paso para alguien que abre el dashboard sin haber visto el proyecto antes.
// No sustituye a la documentación técnica (docs/ARCHITECTURE_LEAN.md) — es
// la versión de un párrafo por concepto, en español llano, pensada para
// leerse en 3 minutos.

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4">
      <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-accent-600 text-sm font-bold text-white dark:bg-accent-500">
        {n}
      </div>
      <div className="-ml-4 mt-1 border-l border-border-subtle pb-6 pl-4">
        <p className="mb-1 font-semibold text-foreground">{title}</p>
        <div className="space-y-2 text-sm text-text-secondary">{children}</div>
      </div>
    </div>
  );
}

export default function ComoFuncionaPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-4 sm:px-6">
      <Nav active="/como-funciona" />
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">Cómo funciona</h1>
        <p className="mt-1 text-sm text-text-secondary">Qué hace el sistema, paso a paso, sin dar nada por sabido.</p>
      </header>

      <section className="mb-8">
        <p className="mb-4 text-sm text-text-secondary">
          Cada noche, el sistema recorre este proceso para cada empresa que presenta un documento oficial ante la SEC (el regulador de
          bolsa de EE.UU.) o recibe una decisión de la FDA (el regulador de medicamentos). El objetivo NO es adivinar el futuro — es
          detectar cuándo una noticia real todavía no se ha reflejado del todo en el precio, y medir con cuánta confianza se puede decir
          eso.
        </p>
      </section>

      <section>
        <Step n={1} title="Detectar el evento">
          <p>
            Cada noche se revisan los documentos oficiales publicados el día anterior (resultados trimestrales, cambios de dirección,
            contratos importantes, decisiones de la FDA...). Se clasifican por tipo automáticamente.
          </p>
        </Step>

        <Step n={2} title="¿Es esto una sorpresa? (Novelty)">
          <p>
            Si la empresa ya había avisado de esto antes, o el precio ya se movió en los días previos anticipándolo, la “sorpresa” es
            baja — y algo que el mercado ya sabe no da ventaja. Se mide qué tan nuevo es realmente el evento.
          </p>
        </Step>

        <Step n={3} title="El debate: Bull vs Bear vs Juez">
          <p>
            Un modelo de IA construye la mejor tesis <strong>a favor</strong> (Bull) y otro la mejor tesis <strong>en contra</strong>{" "}
            (Bear) del evento — cada uno defendiendo su lado con los hechos disponibles, sin inventar cifras. Un tercer modelo (el Juez)
            evalúa el debate y decide qué lado convence más y con cuánta seguridad.
          </p>
        </Step>

        <Step n={4} title="¿Qué pasó otras veces con eventos parecidos?">
          <p>
            Se buscan eventos históricos del mismo tipo (mismo tipo de anuncio, en el pasado) y se mide cuánto se movió el precio en esos
            casos. Cuantos menos casos históricos parecidos haya, menos se confía en la magnitud estimada — con pocos ejemplos, la
            estimación se acerca a la media general de esa categoría en vez de fiarse de una muestra pequeña.
          </p>
        </Step>

        <Step n={5} title="Valor esperado (EV)">
          <p>
            Se combina la dirección y fuerza del veredicto del Juez con la magnitud típica histórica, para estimar cuánto se podría ganar
            o perder, en promedio, operando esta situación.
          </p>
        </Step>

        <Step n={6} title="¿Merece la pena operar, o mejor abstenerse?">
          <p>
            Antes de decidir “operar”, el sistema comprueba 7 condiciones: ¿es realmente una sorpresa?, ¿el Juez tiene suficiente
            seguridad?, ¿el valor esperado compensa las comisiones?, ¿hay señales de que el dato es poco fiable?, ¿la acción tiene
            suficiente liquidez?... Si CUALQUIERA falla, la decisión es <strong>no operar</strong> — abstenerse es un resultado válido,
            no un fallo del sistema.
          </p>
        </Step>

        <Step n={7} title="Confirmación técnica y plan">
          <p>
            Que el evento sea bueno no basta: el gráfico tiene que acompañar. Para cada señal se revisan tendencia (medias de 20, 50 y
            200 sesiones, ADX), impulso (RSI, MACD, Stoch RSI), volatilidad (Bollinger, ATR) y volumen (OBV, VWAP, volumen del día frente
            a su media), y se buscan los niveles donde el precio suele frenar: máximos y mínimos recientes, pivots y Fibonacci.
          </p>
          <p>
            Con eso se fija un plan: entrada, stop justo detrás de un soporte real, objetivo en la siguiente resistencia, tamaño máximo
            según la volatilidad y una puntuación de 0 a 100. Si el riesgo/beneficio no llega a 1:2, o si el stop no se apoya en ningún
            nivel, la señal se marca como no apta aunque el evento sea bueno.
          </p>
        </Step>

        <Step n={8} title="Seguimiento de resultados">
          <p>
            Cada señal se mide con reglas realistas (comisiones, entrada al día siguiente, stop-loss, objetivo de beneficio) de dos
            formas: aplicada a todos los eventos pasados (<strong>Cartera</strong>) y, desde que se publica, con los precios reales de
            mercado (<strong>Historial</strong>). Así se ve si lo que funcionó en el pasado se sigue cumpliendo.
          </p>
        </Step>

        <Step n={9} title="¿Realmente funciona esto?">
          <p>
            Por último, se audita el propio sistema: ¿el tipo de evento mueve el precio de forma estadísticamente real, o podría ser
            ruido? ¿El sistema sabe cuándo confiar en sí mismo (si dice “80% seguro”, acierta de verdad el 80% de las veces)? ¿El
            resultado se mantiene si suben las comisiones o cambia el mercado? Todo esto está en la pestaña{" "}
            <strong>Fiabilidad</strong>
          </p>
        </Step>
      </section>

      <section className="mt-8 border-t border-border-subtle pt-6">
        <p className="mb-2 font-semibold text-foreground">Aparte: el análisis de largo plazo</p>
        <p className="mb-2 text-sm text-text-secondary">
          Todo lo de arriba va de <strong className="text-foreground">eventos</strong> y horizonte de días. La pestaña{" "}
          <strong className="text-foreground">Largo plazo</strong> responde una pregunta completamente distinta:{" "}
          <em>¿es este un buen negocio a un precio razonable?</em>, con horizonte de años.
        </p>
        <p className="text-sm text-text-secondary">
          No mira noticias ni gráficos: descarga las <strong className="text-foreground">cuentas anuales auditadas</strong> que cada
          empresa presenta ante la SEC (gratis, en formato XBRL) y puntúa cinco cosas: cuánto gana sobre su capital, cuánta deuda
          arrastra, si el beneficio se convierte en caja real, si crece, y si la acción está cara. Es el tipo de análisis que se hace
          para comprar un negocio, no para especular con una noticia.
        </p>
      </section>

      <section className="mt-8 border-t border-border-subtle pt-6">
        <p className="mb-2 font-semibold text-foreground">Reglas que el sistema nunca rompe</p>
        <ul className="list-inside list-disc space-y-1 text-sm text-text-secondary">
          <li>Nunca usa información que no existía en el momento de la decisión (nada de “trampa” mirando al futuro).</li>
          <li>Nunca inventa un número que no pueda calcular — si un dato no está disponible, lo dice, no lo estima a ciegas.</li>
          <li>Prefiere decir “no operar” antes que fingir seguridad que no tiene.</li>
        </ul>
      </section>
    </main>
  );
}
