"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { buttonPrimary, buttonSecondary, card, ErrorNote, inputClass, labelClass } from "@/components/ui";
import { api } from "@/lib/api";
import type { Entity, EntityDetail } from "@/lib/types";

type Panel = "none" | "menu" | "edit" | "delete";

/** The owner's actions on an entity: upload, edit its name and description, delete it. */
export function EntityActions({ entity }: { entity: EntityDetail }) {
  const [panel, setPanel] = useState<Panel>("none");
  return (
    // `contents`: the two buttons sit in the page's row of buttons as items of their own, so they take
    // the same size as the rest; the menu and panels hang from the Manage button's own box.
    <div className="contents">
      <Link href={`/entities/${entity.id}/upload`} className={buttonPrimary}>
        Upload reviews
      </Link>
      <div className="relative">
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={panel === "menu"}
        onClick={() => setPanel(panel === "menu" ? "none" : "menu")}
        className={`${buttonSecondary} w-full gap-1.5`}
      >
        Manage
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
      </button>
      {panel === "menu" && (
        <div className={`${card} absolute top-12 right-0 z-10 flex min-w-[180px] flex-col p-1.5 shadow-lg`}>
          <button type="button" onClick={() => setPanel("edit")} className="rounded-md px-3 py-2.5 text-left hover:bg-subtle">
            Edit details
          </button>
          <button
            type="button"
            onClick={() => setPanel("delete")}
            className="rounded-md px-3 py-2.5 text-left text-neg-fg hover:bg-subtle"
          >
            Delete entity…
          </button>
        </div>
      )}
      {panel === "edit" && <EditPanel entity={entity} onClose={() => setPanel("none")} />}
      {panel === "delete" && <DeletePanel entity={entity} onClose={() => setPanel("none")} />}
      </div>
    </div>
  );
}

function EditPanel({ entity, onClose }: { entity: EntityDetail; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(entity.name);
  const [description, setDescription] = useState(entity.description ?? "");
  const save = useMutation({
    mutationFn: () =>
      api<Entity>(`/entities/${entity.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name, description: description.trim() || null }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["entity", String(entity.id)] });
      queryClient.invalidateQueries({ queryKey: ["entities"] });
      onClose();
    },
  });
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
      className={`${card} absolute top-12 right-0 z-10 flex w-[min(420px,calc(100vw-32px))] flex-col gap-4 p-5 shadow-lg`}
    >
      <h2 className="font-semibold">Edit details</h2>
      <ErrorNote error={save.error} />
      <div className="flex flex-col gap-1.5">
        <label htmlFor="edit-name" className={labelClass}>
          Name
        </label>
        <input id="edit-name" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} className={inputClass} />
      </div>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="edit-description" className={labelClass}>
          Description
        </label>
        <textarea
          id="edit-description"
          rows={3}
          maxLength={1000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className={`${inputClass} h-auto py-2.5`}
        />
        <span className="text-[13px] text-muted">Changes apply to analyses from now on, not to themes already found.</span>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className={buttonSecondary}>
          Cancel
        </button>
        <button type="submit" disabled={save.isPending} className={buttonPrimary}>
          Save
        </button>
      </div>
    </form>
  );
}

function DeletePanel({ entity, onClose }: { entity: EntityDetail; onClose: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => api<void>(`/entities/${entity.id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["entity", String(entity.id)] });
      queryClient.invalidateQueries({ queryKey: ["entities"] });
      router.push("/");
    },
  });
  return (
    <div className={`${card} absolute top-12 right-0 z-10 flex w-[min(420px,calc(100vw-32px))] flex-col gap-4 p-5 shadow-lg`}>
      <h2 className="font-semibold">Delete {entity.name}?</h2>
      <p className="text-sm text-muted">
        This deletes its {entity.review_count.toLocaleString("en-US")} reviews, its uploads, and all of its results. It
        can&apos;t be undone.
      </p>
      <ErrorNote error={remove.error} />
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className={buttonSecondary}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => remove.mutate()}
          disabled={remove.isPending}
          className="inline-flex h-11 items-center rounded-lg bg-[#c2410c] px-4 font-semibold text-white disabled:opacity-50"
        >
          Delete
        </button>
      </div>
    </div>
  );
}
