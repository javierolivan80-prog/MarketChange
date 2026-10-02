import Link from "next/link";
import { StatePage, StateBox } from "@/components/ui/PageState";

export default function NotFound() {
  return (
    <StatePage active="" title="Página no encontrada">
      <StateBox title="Esta dirección no existe.">
        <Link href="/" className="text-accent-700 hover:underline dark:text-accent-400">
          Volver al resumen →
        </Link>
      </StateBox>
    </StatePage>
  );
}
