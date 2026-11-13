import { card } from "@/components/ui";
import { isActive } from "@/lib/queries";
import type { EntityDetail } from "@/lib/types";

/**
 * In place of results for an entity with no themes yet: analysis is under way, or hasn't run. Pages
 * reached by a link (All reviews, the period comparison) show it rather than empty filters.
 */
export function NotAnalyzed({ entity, what }: { entity: EntityDetail; what: string }) {
  return (
    <p className={`${card} p-6`}>
      {isActive(entity.latest_run)
        ? `Analysis of ${entity.name} is under way. ${what} will appear here when it finishes.`
        : `${entity.name} hasn't been analyzed yet, so there is nothing to show here. ${what} will appear once its reviews are analyzed.`}
    </p>
  );
}
