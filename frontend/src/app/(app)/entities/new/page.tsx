"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  BackLink,
  buttonPrimary,
  buttonSecondary,
  card,
  ErrorNote,
  inputClass,
  labelClass,
  Page,
  PageTitle,
} from "@/components/ui";
import { api } from "@/lib/api";
import type { Entity } from "@/lib/types";

export default function NewEntityPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api<Entity>("/entities", {
        method: "POST",
        body: JSON.stringify({ name, description: description.trim() || null }),
      }),
    onSuccess: (entity) => {
      queryClient.invalidateQueries({ queryKey: ["entities"] });
      router.push(`/entities/${entity.id}/upload`);
    },
  });

  return (
    <Page width="max-w-[640px]">
      <BackLink href="/">Entities</BackLink>
      <div className="mt-2 mb-6">
        <PageTitle>New entity</PageTitle>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
        className={`${card} flex flex-col gap-5 p-6`}
      >
        <ErrorNote error={create.error} />
        <div className="flex flex-col gap-1.5">
          <label htmlFor="entity-name" className={labelClass}>
            Name
          </label>
          <input
            id="entity-name"
            required
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Amazon Fire Tablet 7-inch (8 GB)"
            className={inputClass}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="entity-description" className={labelClass}>
            Description <span className="font-normal text-muted">(optional)</span>
          </label>
          <textarea
            id="entity-description"
            rows={3}
            maxLength={1000}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="A low-cost 7-inch Wi-Fi tablet made by Amazon, reviewed by buyers"
            className={`${inputClass} h-auto resize-y py-2.5`}
          />
          <span className="text-[13px] text-muted">
            Gives the analysis context, such as &quot;a Cajun restaurant in New Orleans&quot; or &quot;a budgeting
            app for iOS&quot;.
          </span>
        </div>
        <div className="flex flex-wrap justify-end gap-3">
          <Link href="/" className={buttonSecondary}>
            Cancel
          </Link>
          <button type="submit" disabled={create.isPending} className={buttonPrimary}>
            {create.isPending ? "Creating…" : "Create and upload reviews"}
          </button>
        </div>
      </form>
    </Page>
  );
}
