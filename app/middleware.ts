// middleware.ts — acceso privado opcional al panel.
//
// Hasta ahora, cualquiera con la URL de Vercel veía todas las señales, la
// cartera y el razonamiento de la IA: no había ninguna capa de acceso. Para
// un panel personal, HTTP Basic Auth es la medida mínima proporcionada (sin
// base de datos de usuarios, sin dependencias): se activa definiendo
// DASHBOARD_USER y DASHBOARD_PASSWORD en Vercel. Sin ellas, el panel sigue
// siendo público como antes — no rompe ningún despliegue existente.
import { NextResponse, type NextRequest } from "next/server";

export const config = {
  // Todo menos los estáticos de Next y el favicon.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

// Comparación en tiempo constante: evita filtrar la contraseña carácter a
// carácter por diferencias de tiempo de respuesta.
function safeEqual(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

export function middleware(req: NextRequest) {
  const user = process.env.DASHBOARD_USER;
  const password = process.env.DASHBOARD_PASSWORD;
  if (!user || !password) return NextResponse.next();

  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const decoded = atob(header.slice(6));
      const sep = decoded.indexOf(":");
      if (sep >= 0 && safeEqual(decoded.slice(0, sep), user) && safeEqual(decoded.slice(sep + 1), password)) {
        return NextResponse.next();
      }
    } catch {
      // base64 inválido: se trata como credenciales incorrectas.
    }
  }

  return new NextResponse("Acceso restringido.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="MarketChange", charset="UTF-8"' },
  });
}
