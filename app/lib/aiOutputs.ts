// aiOutputs.ts — separa el debate de IA de verdad de los rellenos.
//
// Cuando una regla objetiva ya garantiza NO_TRADE, el pipeline no llama a la
// IA y guarda en bull/bear/judge el mismo marcador
// {skipped_no_llm_needed: true, reason} (event_analysis_pipeline.py). La
// interfaz lo trataba como un veredicto real y leía judge.net_conviction, que
// no existe: desplegar esas filas rompía la página. Aquí el marcador se
// convierte en "no hubo debate, y este es el motivo", y bull/bear/judge solo
// llegan a la interfaz si son un análisis de la IA.
import type { BearOutput, BullOutput, JudgeOutput } from "./queries";

type Skipped = { skipped_no_llm_needed: true; reason?: string };

function isSkipped(v: unknown): v is Skipped {
  return typeof v === "object" && v !== null && (v as { skipped_no_llm_needed?: unknown }).skipped_no_llm_needed === true;
}

export function splitAiOutputs(
  bull: unknown,
  bear: unknown,
  judge: unknown,
): { bull_output: BullOutput | null; bear_output: BearOutput | null; judge_output: JudgeOutput | null; ai_skipped_reason: string | null } {
  const skipped = [bull, bear, judge].find(isSkipped);
  return {
    bull_output: isSkipped(bull) ? null : ((bull as BullOutput | null) ?? null),
    bear_output: isSkipped(bear) ? null : ((bear as BearOutput | null) ?? null),
    judge_output: isSkipped(judge) ? null : ((judge as JudgeOutput | null) ?? null),
    ai_skipped_reason: skipped ? (skipped.reason?.trim() || "") : null,
  };
}
