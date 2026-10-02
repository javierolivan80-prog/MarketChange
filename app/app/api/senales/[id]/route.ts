// GET /api/senales/:id — detalle de una señal para la fila desplegable de la
// tabla (que ya no lleva el razonamiento del LLM embebido en el HTML).
// Protegido por el mismo middleware que el resto del panel.
import { NextResponse } from "next/server";
import { isDatabaseConfigured } from "@/lib/db";
import { getRecommendedVersion, getSignalDetail } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!isDatabaseConfigured()) return NextResponse.json({ error: "not_configured" }, { status: 503 });
  const { id } = await params;
  const eventId = Number(id);
  if (!/^\d+$/.test(id) || !Number.isSafeInteger(eventId)) return NextResponse.json({ error: "bad_id" }, { status: 400 });

  const recommended = await getRecommendedVersion();
  const detail = await getSignalDetail(eventId, recommended);
  if (!detail) return NextResponse.json({ error: "not_found" }, { status: 404 });
  return NextResponse.json({ detail, recommended });
}
