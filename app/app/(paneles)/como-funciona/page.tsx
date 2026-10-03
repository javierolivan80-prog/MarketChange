import { Nav } from "@/components/Nav";
import { getT } from "@/lib/locale";

export async function generateMetadata() {
  const { t } = await getT();
  return { title: t("Cómo funciona", "How it works") };
}

// como-funciona/page.tsx — página nueva, sin datos: explica el motor paso a
// paso para alguien que abre el dashboard sin haber visto el proyecto antes.
// No sustituye a la documentación técnica (docs/ARCHITECTURE_LEAN.md) — es
// la versión de un párrafo por concepto, en lenguaje llano, pensada para
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

export default async function ComoFuncionaPage() {
  const { t } = await getT();
  const strong = (text: string, className?: string) => <strong className={className}>{text}</strong>;

  return (
    <main className="mx-auto max-w-3xl px-4 py-4 sm:px-6">
      <Nav active="/como-funciona" />
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{t("Cómo funciona", "How it works")}</h1>
        <p className="mt-1 text-sm text-text-secondary">
          {t("Qué hace el sistema, paso a paso, sin dar nada por sabido.", "What the system does, step by step, assuming no prior knowledge.")}
        </p>
      </header>

      <section className="mb-8">
        <p className="mb-4 text-sm text-text-secondary">
          {t(
            "Cada noche, el sistema recorre este proceso para cada empresa que presenta un documento oficial ante la SEC (el regulador de bolsa de EE.UU.) o recibe una decisión de la FDA (el regulador de medicamentos). El objetivo NO es adivinar el futuro — es detectar cuándo una noticia real todavía no se ha reflejado del todo en el precio, y medir con cuánta confianza se puede decir eso.",
            "Every night, the system runs this process for every company that files an official document with the SEC (the US securities regulator) or receives a decision from the FDA (the drug regulator). The goal is NOT to guess the future — it is to detect when real news has not yet been fully reflected in the price, and to measure how confidently that can be said.",
          )}
        </p>
      </section>

      <section>
        <Step n={1} title={t("Detectar el evento", "Detect the event")}>
          <p>
            {t(
              "Cada noche se revisan los documentos oficiales publicados el día anterior (resultados trimestrales, cambios de dirección, contratos importantes, decisiones de la FDA...). Se clasifican por tipo automáticamente.",
              "Every night, the official documents published the previous day are reviewed (quarterly results, management changes, major contracts, FDA decisions...). They are classified by type automatically.",
            )}
          </p>
        </Step>

        <Step n={2} title={t("¿Es esto una sorpresa? (Novelty)", "Is this a surprise? (Novelty)")}>
          <p>
            {t(
              "Si la empresa ya había avisado de esto antes, o el precio ya se movió en los días previos anticipándolo, la “sorpresa” es baja — y algo que el mercado ya sabe no da ventaja. Se mide qué tan nuevo es realmente el evento.",
              "If the company had already flagged this, or the price had already moved in the days before in anticipation, the “surprise” is low — and something the market already knows gives no edge. The system measures how new the event really is.",
            )}
          </p>
        </Step>

        <Step n={3} title={t("El debate: Bull vs Bear vs Juez", "The debate: Bull vs Bear vs Judge")}>
          <p>
            {t("Un modelo de IA construye la mejor tesis ", "One AI model builds the best case ")}
            {strong(t("a favor", "for"))}
            {t(" (Bull) y otro la mejor tesis ", " the event (Bull) and another the best case ")}
            {strong(t("en contra", "against"))}{" "}
            {t(
              "(Bear) del evento — cada uno defendiendo su lado con los hechos disponibles, sin inventar cifras. Un tercer modelo (el Juez) evalúa el debate y decide qué lado convence más y con cuánta seguridad.",
              "it (Bear) — each defending its side with the available facts, without inventing figures. A third model (the Judge) weighs the debate and decides which side is more convincing, and how confidently.",
            )}
          </p>
        </Step>

        <Step n={4} title={t("¿Qué pasó otras veces con eventos parecidos?", "What happened with similar events before?")}>
          <p>
            {t(
              "Se buscan eventos históricos del mismo tipo (mismo tipo de anuncio, en el pasado) y se mide cuánto se movió el precio en esos casos. Cuantos menos casos históricos parecidos haya, menos se confía en la magnitud estimada — con pocos ejemplos, la estimación se acerca a la media general de esa categoría en vez de fiarse de una muestra pequeña.",
              "Past events of the same type (the same kind of announcement) are looked up, and the price move in those cases is measured. The fewer similar past cases there are, the less the estimated size is trusted — with few examples, the estimate is pulled towards the overall average for that category instead of relying on a small sample.",
            )}
          </p>
        </Step>

        <Step n={5} title={t("Valor esperado (EV)", "Expected value (EV)")}>
          <p>
            {t(
              "Se combina la dirección y fuerza del veredicto del Juez con la magnitud típica histórica, para estimar cuánto se podría ganar o perder, en promedio, operando esta situación.",
              "The direction and strength of the Judge's verdict are combined with the typical historical move to estimate how much could be gained or lost, on average, by trading this situation.",
            )}
          </p>
        </Step>

        <Step n={6} title={t("¿Merece la pena operar, o mejor abstenerse?", "Is it worth trading, or better to stay out?")}>
          <p>
            {t(
              "Antes de decidir “operar”, el sistema comprueba 7 condiciones: ¿es realmente una sorpresa?, ¿el Juez tiene suficiente seguridad?, ¿el valor esperado compensa las comisiones?, ¿hay señales de que el dato es poco fiable?, ¿la acción tiene suficiente liquidez?... Si CUALQUIERA falla, la decisión es ",
              "Before deciding to “trade”, the system checks 7 conditions: is it really a surprise? is the Judge confident enough? does the expected value cover the fees? are there signs the data is unreliable? is the stock liquid enough?... If ANY of them fails, the decision is ",
            )}
            {strong(t("no operar", "not to trade"))}
            {t(" — abstenerse es un resultado válido, no un fallo del sistema.", " — staying out is a valid outcome, not a system failure.")}
          </p>
        </Step>

        <Step n={7} title={t("Confirmación técnica y plan", "Technical confirmation and plan")}>
          <p>
            {t(
              "Que el evento sea bueno no basta: el gráfico tiene que acompañar. Para cada señal se revisan tendencia (medias de 20, 50 y 200 sesiones, ADX), impulso (RSI, MACD, Stoch RSI), volatilidad (Bollinger, ATR) y volumen (OBV, VWAP, volumen del día frente a su media), y se buscan los niveles donde el precio suele frenar: máximos y mínimos recientes, pivots y Fibonacci.",
              "A good event is not enough: the chart has to agree. For each signal the system checks trend (20, 50 and 200-session averages, ADX), momentum (RSI, MACD, Stoch RSI), volatility (Bollinger, ATR) and volume (OBV, VWAP, the day's volume against its average), and looks for the levels where price tends to stall: recent highs and lows, pivots and Fibonacci.",
            )}
          </p>
          <p>
            {t(
              "Con eso se fija un plan: entrada, stop justo detrás de un soporte real, objetivo en la siguiente resistencia, tamaño máximo según la volatilidad y una puntuación de 0 a 100. Si el riesgo/beneficio no llega a 1:2, o si el stop no se apoya en ningún nivel, la señal se marca como no apta aunque el evento sea bueno.",
              "That sets a plan: entry, a stop just behind a real support, a target at the next resistance, a maximum size based on volatility and a score from 0 to 100. If risk/reward falls short of 1:2, or the stop does not sit on any level, the signal is marked as unfit even if the event is good.",
            )}
          </p>
        </Step>

        <Step n={8} title={t("Seguimiento de resultados", "Tracking results")}>
          <p>
            {t(
              "Cada señal se mide con reglas realistas (comisiones, entrada al día siguiente, stop-loss, objetivo de beneficio) de dos formas: aplicada a todos los eventos pasados (",
              "Each signal is measured with realistic rules (fees, entry the next day, stop-loss, profit target) in two ways: applied to all past events (",
            )}
            {strong(t("Cartera", "Portfolio"))}
            {t(") y, desde que se publica, con los precios reales de mercado (", ") and, from the moment it is published, at real market prices (")}
            {strong(t("Historial", "Track record"))}
            {t("). Así se ve si lo que funcionó en el pasado se sigue cumpliendo.", "). That shows whether what worked in the past still holds.")}
          </p>
        </Step>

        <Step n={9} title={t("¿Realmente funciona esto?", "Does this really work?")}>
          <p>
            {t(
              "Por último, se audita el propio sistema: ¿el tipo de evento mueve el precio de forma estadísticamente real, o podría ser ruido? ¿El sistema sabe cuándo confiar en sí mismo (si dice “80% seguro”, acierta de verdad el 80% de las veces)? ¿El resultado se mantiene si suben las comisiones o cambia el mercado? Todo esto está en la pestaña ",
              "Finally, the system audits itself: does the event type move the price in a statistically real way, or could it be noise? Does the system know when to trust itself (if it says “80% sure”, is it really right 80% of the time)? Does the result hold if fees rise or the market changes? All of this is in the ",
            )}
            {strong(t("Fiabilidad", "Reliability"))}
            {t("", " tab.")}
          </p>
        </Step>
      </section>

      <section className="mt-8 border-t border-border-subtle pt-6">
        <p className="mb-2 font-semibold text-foreground">{t("Aparte: el análisis de largo plazo", "Separately: the long-term analysis")}</p>
        <p className="mb-2 text-sm text-text-secondary">
          {t("Todo lo de arriba va de ", "Everything above is about ")}
          {strong(t("eventos", "events"), "text-foreground")}
          {t(" y horizonte de días. La pestaña ", " and a horizon of days. The ")}
          {strong(t("Largo plazo", "Long term"), "text-foreground")}
          {t(" responde una pregunta completamente distinta: ", " page answers a completely different question: ")}
          <em>{t("¿es este un buen negocio a un precio razonable?", "is this a good business at a reasonable price?")}</em>
          {t(", con horizonte de años.", ", with a horizon of years.")}
        </p>
        <p className="text-sm text-text-secondary">
          {t("No mira noticias ni gráficos: descarga las ", "It ignores news and charts: it downloads the ")}
          {strong(t("cuentas anuales auditadas", "audited annual accounts"), "text-foreground")}
          {t(
            " que cada empresa presenta ante la SEC (gratis, en formato XBRL) y puntúa cinco cosas: cuánto gana sobre su capital, cuánta deuda arrastra, si el beneficio se convierte en caja real, si crece, y si la acción está cara. Es el tipo de análisis que se hace para comprar un negocio, no para especular con una noticia.",
            " every company files with the SEC (free, in XBRL format) and scores five things: how much it earns on its equity, how much debt it carries, whether profit turns into real cash, whether it grows, and whether the stock is expensive. It is the kind of analysis you do to buy a business, not to speculate on a piece of news.",
          )}
        </p>
      </section>

      <section className="mt-8 border-t border-border-subtle pt-6">
        <p className="mb-2 font-semibold text-foreground">{t("Reglas que el sistema nunca rompe", "Rules the system never breaks")}</p>
        <ul className="list-inside list-disc space-y-1 text-sm text-text-secondary">
          <li>
            {t(
              "Nunca usa información que no existía en el momento de la decisión (nada de “trampa” mirando al futuro).",
              "It never uses information that did not exist at the time of the decision (no “cheating” by looking ahead).",
            )}
          </li>
          <li>
            {t(
              "Nunca inventa un número que no pueda calcular — si un dato no está disponible, lo dice, no lo estima a ciegas.",
              "It never invents a number it cannot calculate — if a figure is not available, it says so instead of guessing.",
            )}
          </li>
          <li>{t("Prefiere decir “no operar” antes que fingir seguridad que no tiene.", "It would rather say “don't trade” than fake a confidence it does not have.")}</li>
        </ul>
      </section>
    </main>
  );
}
