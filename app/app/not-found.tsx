import Link from "next/link";
import { StatePage, StateBox } from "@/components/ui/PageState";
import { getT } from "@/lib/locale";

export default async function NotFound() {
  const { t } = await getT();
  return (
    <StatePage active="" title={t("Página no encontrada", "Page not found")}>
      <StateBox title={t("Esta dirección no existe.", "This address does not exist.")}>
        <Link href="/" className="text-accent-700 hover:underline dark:text-accent-400">
          {t("Volver al resumen →", "Back to the overview →")}
        </Link>
      </StateBox>
    </StatePage>
  );
}
