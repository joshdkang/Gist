"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { buttonPrimary, card, ErrorNote, Loading, Page, PageTitle } from "@/components/ui";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { Entity } from "@/lib/types";

type Tab = "mine" | "public";
const TABS: { key: Tab; label: string }[] = [
  { key: "mine", label: "My entities" },
  { key: "public", label: "Samples" },
];

function EntityCard({ entity }: { entity: Entity }) {
  const analyzed = entity.theme_count > 0;
  return (
    <Link
      href={`/entities/${entity.id}`}
      className={`${card} flex flex-col gap-1.5 p-[18px] hover:border-line-strong ${analyzed ? "" : "border-dashed"}`}
    >
      <span className="text-base font-semibold">{entity.name}</span>
      {entity.description && <span className="text-sm text-muted">{entity.description}</span>}
      <span className="mt-1.5 flex items-center gap-2 text-[13px] text-muted">
        {analyzed && <span className="size-2 rounded-full bg-pos" aria-hidden="true" />}
        {formatNumber(entity.review_count)} reviews ·{" "}
        {analyzed ? `${entity.theme_count} themes` : "not analyzed yet"}
      </span>
    </Link>
  );
}

export default function EntitiesPage() {
  const [tab, setTab] = useState<Tab>("mine");
  const entities = useQuery({
    queryKey: ["entities", tab],
    queryFn: () => api<Entity[]>(`/entities?scope=${tab}&limit=100`),
  });
  // Analyzed entities first, so the samples with results lead.
  const sorted = [...(entities.data ?? [])].sort(
    (a, b) => Number(b.theme_count > 0) - Number(a.theme_count > 0),
  );

  return (
    <Page>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <PageTitle>Entities</PageTitle>
        <Link href="/entities/new" className={`${buttonPrimary} h-10`}>
          New entity
        </Link>
      </div>
      <div role="tablist" className="mb-5 flex gap-1 border-b border-line">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-3.5 py-2.5 font-medium ${
              tab === key ? "border-fg text-fg" : "border-transparent text-muted hover:text-fg"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "public" && (
        <p className="mb-4 text-muted">
          Restaurants from the Yelp open dataset. Anyone signed in can browse them; they are read only.
        </p>
      )}
      <ErrorNote error={entities.error} />
      {entities.isPending && <Loading />}
      {entities.isSuccess && sorted.length === 0 && tab === "mine" && (
        <div className={`${card} flex flex-col items-start gap-3 p-6`}>
          <p className="font-semibold">You have no entities yet.</p>
          <p className="text-muted">
            Create one for anything that gets reviewed, upload a CSV of its reviews, and Gist finds what they
            keep saying. Or browse the samples to see results first.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link href="/entities/new" className={buttonPrimary}>
              Create an entity
            </Link>
            <button type="button" onClick={() => setTab("public")} className="px-2 text-link hover:underline">
              Browse samples
            </button>
          </div>
        </div>
      )}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
        {sorted.map((entity) => (
          <EntityCard key={entity.id} entity={entity} />
        ))}
      </div>
    </Page>
  );
}
