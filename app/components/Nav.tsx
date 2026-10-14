"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { LanguageToggle } from "@/components/ui/LanguageToggle";
import { useT } from "@/components/i18n/LocaleProvider";

// Nav.tsx — barra de navegación.
//
// La pestaña activa se deduce de la URL (usePathname): así error.tsx y
// not-found.tsx, que no saben en qué página están, también marcan bien la
// sección.
//
// Vive en el layout raíz (Tanda 12), con el mismo ancho en todas las
// páginas: antes cada página la pintaba dentro de su propio <main> (3xl, 5xl
// o 7xl) y en pantallas anchas la cabecera saltaba de sitio al cambiar de
// pestaña o al llegar la página tras la pantalla de carga.
//
// Antes la cabecera usaba márgenes negativos (-mx-6) para "salirse" del
// padding de la página — con páginas de padding 4 eso la hacía 8px más ancha
// que la pantalla y provocaba scroll horizontal en móvil.
// Largo plazo (ranking fundamental) es otro producto, con otro horizonte: va
// en el pie de página, no compitiendo con el flujo principal de señales.
const TABS = [
  { href: "/", es: "Resumen", en: "Overview" },
  { href: "/senales", es: "Señales", en: "Signals" },
  { href: "/historial", es: "Historial", en: "Track record" },
  { href: "/cartera", es: "Cartera", en: "Portfolio" },
  { href: "/funciona", es: "Fiabilidad", en: "Reliability" },
  { href: "/como-funciona", es: "Cómo funciona", en: "How it works" },
] as const;

// Aspecto de la pestaña activa: lo lleva la copia recortada y, hasta que
// esta se mide al hidratar (o sin JS), la propia pestaña. El fondo va en el
// contenedor de la copia, no en cada pestaña: así, a mitad del recorrido,
// lo recortado es una sola píldora y no trozos de dos. Con más contraste la
// píldora gris (~1,3:1 sobre el fondo) no bastaba: se invierte (fondo del
// color del texto). Con colores forzados el sistema anula el relleno: se
// usan sus colores de selección (Highlight / HighlightText).
const ACTIVA_FONDO =
  "bg-fill-strong contrast-more:bg-foreground forced-colors:forced-color-adjust-none forced-colors:bg-[color:Highlight]";
const ACTIVA_TEXTO =
  "text-foreground contrast-more:text-background forced-colors:forced-color-adjust-none forced-colors:text-[color:HighlightText]";

export function Nav() {
  const pathname = usePathname();
  const current = pathname ?? "";
  const t = useT();
  // En móvil las pestañas no caben (Tanda 12): la activa se desplaza a la
  // vista y un degradado en el borde avisa de que hay más mientras quede
  // contenido por ese lado.
  const tira = useRef<HTMLDivElement>(null);
  const [mas, setMas] = useState({ izquierda: false, derecha: false });

  function medir() {
    const el = tira.current;
    if (!el) return;
    setMas({ izquierda: el.scrollLeft > 4, derecha: el.scrollLeft + el.clientWidth < el.scrollWidth - 4 });
  }

  // Posiciones medidas en pantalla y relativas a la tira (offsetLeft lo es
  // al documento); el margen es el ancho del degradado, para que la activa
  // no quede debajo. Sin scrollIntoView: también movería la página en
  // vertical si la cabecera no está a la vista.
  function llevarActivaALaVista() {
    const el = tira.current;
    const activa = el?.querySelector<HTMLElement>('[aria-current="page"]');
    if (el && activa) {
      const margen = 32;
      const caja = el.getBoundingClientRect();
      const a = activa.getBoundingClientRect();
      if (a.left < caja.left + margen) el.scrollLeft -= caja.left + margen - a.left;
      else if (a.right > caja.right - margen) el.scrollLeft += a.right - (caja.right - margen);
    }
  }

  // Indicador de la pestaña activa (Tanda 12c, guía de Apple): una píldora
  // que se desliza con un muelle de una pestaña a otra, como un control
  // segmentado, en vez de un subrayado que salta. Posición medida con
  // offsetLeft/offsetTop respecto a la tira (que es `relative`), así que se
  // desplaza con ella al hacer scroll. La primera medida no se anima.
  //
  // Tanda 12d (técnica de Emil para pestañas): en vez de mover y estirar una
  // píldora (animar width/height obliga a recalcular el layout en cada
  // fotograma), hay una COPIA de la tira con el estilo activo en todas las
  // pestañas, recortada con clip-path para que solo se vea la activa; al
  // cambiar de pestaña se anima el recorte. Así el color del texto cambia a
  // la vez que se mueve la píldora, sin dos estados superpuestos. Todas las
  // pestañas van en el mismo peso (font-medium) para que copia y original
  // midan lo mismo. Es un movimiento de un sitio a otro: el muelle sin
  // rebote de Apple (mover y recolocar) en 235 ms, que se mueve desde el
  // primer fotograma; con el teclado, sin animación; con movimiento
  // reducido, salta (transición estática).
  //
  // Solo se anima al cambiar de pestaña. Al girar el móvil o cambiar de
  // idioma la copia se recoloca de golpe, así que el recorte también salta
  // (si se deslizara, durante 240 ms recortaría el texto en sitio viejo). Una
  // medida que no cambia nada (p. ej. al aparecer la barra de scroll a mitad
  // de una animación) no toca el estado, para no cortar la animación en curso.
  const copia = useRef<HTMLDivElement>(null);
  const [indicador, setIndicador] = useState<{ x: number; y: number; w: number; h: number; cw: number; ch: number; animar: boolean } | null>(null);
  function medirIndicador(animar: boolean) {
    const activa = tira.current?.querySelector<HTMLElement>('[aria-current="page"]');
    const c = copia.current;
    if (!activa || !c) return setIndicador(null);
    const nuevo = { x: activa.offsetLeft, y: activa.offsetTop, w: activa.offsetWidth, h: activa.offsetHeight, cw: c.offsetWidth, ch: c.offsetHeight };
    setIndicador((prev) =>
      prev && prev.x === nuevo.x && prev.y === nuevo.y && prev.w === nuevo.w && prev.h === nuevo.h && prev.cw === nuevo.cw && prev.ch === nuevo.ch
        ? prev
        : { ...nuevo, animar: animar && prev !== null },
    );
  }

  // Lo activado con el teclado no se anima (ver globals.css): se marca el
  // <html> al pulsar Intro, espacio o tabulador, y se desmarca con el primer
  // clic o toque.
  useEffect(() => {
    const html = document.documentElement;
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " " || e.key === "Tab") html.dataset.teclado = "";
    };
    const puntero = () => {
      delete html.dataset.teclado;
    };
    window.addEventListener("keydown", tecla, true);
    window.addEventListener("pointerdown", puntero, true);
    return () => {
      window.removeEventListener("keydown", tecla, true);
      window.removeEventListener("pointerdown", puntero, true);
    };
  }, []);

  // Antes del pintado (useLayoutEffect): al navegar, el recorte empieza a
  // moverse en el mismo fotograma en que cambia la activa; al cambiar de
  // idioma (t cambia con él), la copia y el recorte se recolocan juntos. Con
  // el ResizeObserver solo, el recorte quedaba un fotograma descolocado.
  useLayoutEffect(() => {
    llevarActivaALaVista();
    medir();
    medirIndicador(true);
  }, [current]);
  useLayoutEffect(() => {
    llevarActivaALaVista();
    medir();
    medirIndicador(false);
  }, [t]);

  // Se observa la tira y cada pestaña: al girar el móvil cambia el ancho de
  // la tira, y al cambiar de idioma cambia el de las pestañas (la tira, que
  // es flex-1, no). En los dos casos hay que volver a medir el indicador y
  // el degradado y volver a traer la activa a la vista.
  useEffect(() => {
    const el = tira.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observador = new ResizeObserver(() => {
      llevarActivaALaVista();
      medir();
      medirIndicador(false);
    });
    observador.observe(el);
    el.querySelectorAll("a").forEach((a) => observador.observe(a));
    return () => observador.disconnect();
  }, []);

  // Al desplazarse: (1) borde difuminado bajo la barra solo cuando el
  // contenido pasa por debajo (guía de Apple: efecto de borde, no línea fija);
  // (2) el título de la página se recoge en la barra cuando el grande queda
  // tapado. La barra está pegada cuando su top real coincide con el `top`
  // negativo del sticky (ver layout.tsx).
  // El texto se conserva al ocultarse, para que salga con el mismo muelle
  // con el que entra en vez de desaparecer de golpe.
  const [titulo, setTitulo] = useState({ texto: "", visible: false });
  const cabecera = useRef<HTMLElement>(null);
  useEffect(() => {
    const banda = cabecera.current?.closest<HTMLElement>(".barra-material");
    if (!banda) return;
    let pendiente = 0;
    function actualizar() {
      pendiente = 0;
      if (!banda) return;
      const topFijo = parseFloat(getComputedStyle(banda).top) || 0;
      const caja = banda.getBoundingClientRect();
      const pegada = window.scrollY > 0 && caja.top <= topFijo + 0.5;
      if (pegada) banda.dataset.pegada = "";
      else delete banda.dataset.pegada;
      const h1 = document.querySelector("main h1");
      const tapado = h1 !== null && h1.getBoundingClientRect().bottom < caja.bottom;
      setTitulo((prev) => (tapado ? { texto: (h1!.textContent ?? "").trim(), visible: true } : { ...prev, visible: false }));
    }
    function alDesplazar() {
      if (!pendiente) pendiente = requestAnimationFrame(actualizar);
    }
    actualizar();
    window.addEventListener("scroll", alDesplazar, { passive: true });
    window.addEventListener("resize", alDesplazar);
    // La página puede llegar después (pantalla de carga → contenido) sin que
    // haya scroll: se vuelve a mirar cuando cambia el contenido.
    const cambios = typeof MutationObserver === "undefined" ? null : new MutationObserver(alDesplazar);
    cambios?.observe(document.body, { childList: true, subtree: true });
    return () => {
      window.removeEventListener("scroll", alDesplazar);
      window.removeEventListener("resize", alDesplazar);
      cambios?.disconnect();
      cancelAnimationFrame(pendiente);
    };
  }, [current]);

  const mascara =
    mas.izquierda && mas.derecha
      ? "[mask-image:linear-gradient(to_right,transparent,black_2rem,black_calc(100%-2rem),transparent)]"
      : mas.derecha
        ? "[mask-image:linear-gradient(to_right,black_calc(100%-2rem),transparent)]"
        : mas.izquierda
          ? "[mask-image:linear-gradient(to_right,transparent,black_2rem)]"
          : "";

  return (
    <header ref={cabecera} className="mb-5">
      {/* Por debajo de 360px (móviles de 320) el logo y los huecos se
          reducen: la fila no cabía (311px en 288) y daba scroll horizontal.
          Logo en la fuente del sistema (Tanda 12c), no en monoespaciada y
          mayúsculas. */}
      <div className="flex items-center justify-between gap-4 max-[359px]:gap-2">
        <Link href="/" className="text-[1.0625rem] font-semibold tracking-[-0.01em] text-foreground max-[359px]:text-[0.875rem]">
          MarketChange
        </Link>
        <div className="flex items-center gap-2 max-[359px]:gap-1">
          <LanguageToggle />
          <ThemeToggle />
        </div>
      </div>
      <nav aria-label={t("Secciones", "Sections")} className="mt-2 flex items-center gap-4 pb-2">
        <div ref={tira} onScroll={medir} className={`relative flex min-w-0 flex-1 gap-1 overflow-x-auto [scrollbar-width:none] ${mascara}`}>
          {TABS.map((tab) => {
            const isActive = tab.href === "/" ? current === "/" : current.startsWith(tab.href);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                aria-current={isActive ? "page" : undefined}
                className={`relative whitespace-nowrap rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors duration-(--motion-duration-micro) pointer-coarse:py-3 ${
                  isActive && !indicador ? `${ACTIVA_FONDO} ${ACTIVA_TEXTO}` : "text-text-secondary hover:text-foreground active:bg-fill"
                }`}
              >
                {t(tab.es, tab.en)}
              </Link>
            );
          })}
          {/* Copia con el estilo activo, recortada a la pestaña activa (ver
              arriba). Decorativa: sin eventos, fuera del árbol accesible
              (aria-hidden) y de la búsqueda en la página y la selección
              (inert, select-none), para que cada etiqueta no salga dos veces. */}
          <div
            ref={copia}
            aria-hidden="true"
            inert
            data-anima-con-puntero
            className={`pointer-events-none absolute left-0 top-0 flex select-none gap-1 ${ACTIVA_FONDO} motion-reduce:transition-none ${
              indicador?.animar ? "transition-[clip-path] duration-(--motion-duration-spring) ease-(--motion-ease-spring)" : ""
            } ${indicador ? "" : "invisible"}`}
            style={
              indicador
                ? {
                    clipPath: `inset(${indicador.y}px ${indicador.cw - indicador.x - indicador.w}px ${indicador.ch - indicador.y - indicador.h}px ${indicador.x}px round 9999px)`,
                  }
                : undefined
            }
          >
            {TABS.map((tab) => (
              <span key={tab.href} className={`whitespace-nowrap px-3.5 py-1.5 text-sm font-medium pointer-coarse:py-3 ${ACTIVA_TEXTO}`}>
                {t(tab.es, tab.en)}
              </span>
            ))}
          </div>
        </div>
        {/* Título recogido: duplica el h1 (por eso aria-hidden) y solo se ve
            desde lg (a 768px le quitaba ancho a la tira y la última pestaña
            quedaba cortada); por debajo, la pestaña activa ya dice dónde se
            está. */}
        <span
          aria-hidden="true"
          className={`hidden max-w-[16rem] shrink-0 truncate text-sm font-semibold text-foreground transition-[opacity,translate] ease-(--motion-ease-spring) motion-reduce:transition-opacity lg:block ${
            titulo.visible ? "translate-y-0 opacity-100 duration-(--motion-duration-spring)" : "translate-y-1 opacity-0 duration-(--motion-duration-spring-fast)"
          }`}
        >
          {titulo.texto}
        </span>
      </nav>
    </header>
  );
}
