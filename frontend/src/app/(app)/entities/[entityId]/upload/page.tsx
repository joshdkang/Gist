"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";

import {
  BackLink,
  buttonLink,
  buttonPrimary,
  buttonSecondary,
  card,
  ErrorNote,
  inputBase,
  inputClass,
  labelClass,
  Loading,
  Page,
  PageTitle,
} from "@/components/ui";
import { api } from "@/lib/api";
import {
  buildMapping,
  buildScale,
  guessMapping,
  guessScale,
  MAX_UPLOAD_BYTES,
  MAX_UPLOAD_ROWS,
  type MappedField,
  parseCsv,
  type ParsedCsv,
  type ScaleDraft,
} from "@/lib/csv";
import { formatNumber } from "@/lib/format";
import { useEntity } from "@/lib/queries";
import type { UploadResult } from "@/lib/types";

const NONE = "";
const FIELDS: { key: MappedField; label: string }[] = [
  { key: "title", label: "Title" },
  { key: "rating", label: "Rating" },
  { key: "review_date", label: "Date" },
  { key: "external_id", label: "External ID" },
];
type Fields = { text: string } & Partial<Record<MappedField, string>>;

function Steps({ current }: { current: number }) {
  return (
    <ol className="mb-6 flex flex-wrap gap-x-6 gap-y-2">
      {["Choose a file", "Map columns", "Done"].map((label, index) => {
        const step = index + 1;
        const reached = step <= current;
        return (
          <li key={label} className={`flex items-center gap-2 text-sm font-medium ${step === current ? "" : "text-muted"}`}>
            <span
              className={`inline-flex size-[26px] items-center justify-center rounded-full text-[13px] font-semibold ${
                reached ? "bg-primary text-on-primary" : "bg-subtle text-muted"
              }`}
            >
              {step < current ? "✓" : step}
            </span>
            {label}
          </li>
        );
      })}
    </ol>
  );
}

function ChooseFile({ onParsed }: { onParsed: (file: File, parsed: ParsedCsv) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const [dragging, setDragging] = useState(false);

  async function accept(file: File | undefined) {
    if (!file) return;
    setError(null);
    if (file.size > MAX_UPLOAD_BYTES) return setError(`${file.name} is over the 5 MB limit.`);
    setReading(true);
    try {
      const parsed = await parseCsv(file);
      if (parsed.columns.length === 0 || parsed.rows.length === 0) return setError("The file has no header row or no rows.");
      if (parsed.rows.length > MAX_UPLOAD_ROWS) {
        return setError(
          `The file has ${formatNumber(parsed.rows.length)} rows; the limit is ${formatNumber(MAX_UPLOAD_ROWS)} per upload. Split it into several files.`,
        );
      }
      onParsed(file, parsed);
    } catch {
      setError("That file couldn't be read as CSV.");
    } finally {
      setReading(false);
    }
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        accept(event.dataTransfer.files[0]);
      }}
      className={`flex flex-col items-center gap-3 rounded-xl border-2 border-dashed bg-surface px-6 py-12 text-center ${
        dragging ? "border-pos" : "border-line-strong"
      }`}
    >
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className="text-muted" aria-hidden="true">
        <path d="M12 16V4" />
        <path d="M7 9l5-5 5 5" />
        <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
      </svg>
      <p className="text-base font-semibold">Drop a CSV file here</p>
      <p className="text-sm text-muted">UTF-8 with a header row. Up to 5 MB and 5,000 rows per upload.</p>
      <ErrorNote error={error} />
      <input
        ref={input}
        type="file"
        accept=".csv,text/csv"
        className="sr-only"
        aria-label="Choose a CSV file"
        onChange={(event) => accept(event.target.files?.[0])}
      />
      <button type="button" onClick={() => input.current?.click()} disabled={reading} className={`${buttonPrimary} mt-2`}>
        {reading ? "Reading the file…" : "Choose a file"}
      </button>
    </div>
  );
}

function MapColumns({
  entityId,
  file,
  parsed,
  onBack,
  onUploaded,
}: {
  entityId: string;
  file: File;
  parsed: ParsedCsv;
  onBack: () => void;
  onUploaded: (result: UploadResult) => void;
}) {
  const queryClient = useQueryClient();
  const [fields, setFields] = useState<Fields>(() => guessMapping(parsed));
  const ratingValues = (column?: string) => (column ? parsed.rows.slice(0, 1000).map((row) => row[column] ?? "") : []);
  const [scale, setScale] = useState<ScaleDraft>(() => guessScale(ratingValues(guessMapping(parsed).rating)));
  const [dateFormat, setDateFormat] = useState("");
  const [metadata, setMetadata] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);

  const mapped = new Set(Object.values(fields).filter(Boolean));
  const otherColumns = parsed.columns.filter((column) => !mapped.has(column));
  const preview = parsed.rows.slice(0, 3);
  const sampleDate = fields.review_date ? parsed.rows.find((row) => row[fields.review_date!])?.[fields.review_date] : undefined;

  function setField(field: keyof Fields, column: string) {
    setFields((current) => ({ ...current, [field]: column || undefined }));
    if (field === "rating" && column) setScale(guessScale(ratingValues(column)));
    setMetadata((current) => current.filter((name) => name !== column));
  }

  const upload = useMutation({
    mutationFn: (form: FormData) => api<UploadResult>(`/entities/${entityId}/imports`, { method: "POST", body: form }),
    onSuccess: (result) => {
      for (const key of ["entity", "themes", "ratings", "entities"]) queryClient.invalidateQueries({ queryKey: [key] });
      onUploaded(result);
    },
  });

  function submit() {
    setProblem(null);
    const form = new FormData();
    form.append("file", file);
    form.append("mapping", JSON.stringify(buildMapping(fields, metadata, dateFormat)));
    if (fields.rating) {
      const built = buildScale(scale);
      if (typeof built === "string") return setProblem(built);
      form.append("rating_scale", JSON.stringify(built));
    }
    upload.mutate(form);
  }

  const select = (id: string, value: string | undefined, onChange: (column: string) => void, optional: boolean) => (
    <select id={id} value={value ?? NONE} onChange={(event) => onChange(event.target.value)} className={inputClass}>
      {optional && <option value={NONE}>(none)</option>}
      {parsed.columns.map((column) => (
        <option key={column} value={column}>
          {column}
        </option>
      ))}
    </select>
  );

  return (
    <div className="flex flex-col gap-5">
      <div className={`${card} flex flex-wrap items-center justify-between gap-3 px-5 py-4`}>
        <span>
          <span className="font-semibold">{file.name}</span>{" "}
          <span className="text-sm text-muted">
            {formatNumber(Math.ceil(file.size / 1024))} KB · {formatNumber(parsed.rows.length)} rows
          </span>
        </span>
        <button type="button" onClick={onBack} className={buttonLink}>
          Choose another file
        </button>
      </div>

      <section className={`${card} p-5`}>
        <h2 className="mb-1 text-[17px] font-semibold">Map your columns</h2>
        <p className="mb-4 text-sm text-muted">Guessed from the column names and values. Only the review text is required.</p>
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="map-text" className={labelClass}>
              Review text (required)
            </label>
            {select("map-text", fields.text, (column) => setField("text", column), false)}
          </div>
          {FIELDS.map(({ key, label }) => (
            <div key={key} className="flex flex-col gap-1.5">
              <label htmlFor={`map-${key}`} className={labelClass}>
                {label}
              </label>
              {select(`map-${key}`, fields[key], (column) => setField(key, column), true)}
            </div>
          ))}
        </div>

        {fields.rating && (
          <fieldset className="mt-5 flex flex-col gap-3 rounded-lg bg-subtle px-4 py-3.5">
            <legend className="sr-only">Rating scale</legend>
            <div className="flex flex-wrap items-center gap-3">
              <label htmlFor="scale-type" className={labelClass}>
                Rating scale
              </label>
              <select
                id="scale-type"
                value={scale.type}
                onChange={(event) => setScale({ ...scale, type: event.target.value as ScaleDraft["type"] })}
                className={inputBase}
              >
                <option value="numeric">Numeric range</option>
                <option value="binary">Yes / no</option>
                <option value="labels">Ordered labels</option>
              </select>
              {scale.type === "numeric" && (
                <span className="flex items-center gap-2 text-sm">
                  <label htmlFor="scale-min">from</label>
                  <input id="scale-min" inputMode="decimal" value={scale.min} onChange={(e) => setScale({ ...scale, min: e.target.value })} className={`${inputBase} w-20`} />
                  <label htmlFor="scale-max">to</label>
                  <input id="scale-max" inputMode="decimal" value={scale.max} onChange={(e) => setScale({ ...scale, max: e.target.value })} className={`${inputBase} w-20`} />
                </span>
              )}
            </div>
            {scale.type === "labels" && (
              <div className="flex flex-col gap-1.5">
                <label htmlFor="scale-labels" className={labelClass}>
                  Labels, lowest first, separated by commas
                </label>
                <input id="scale-labels" value={scale.labels} onChange={(e) => setScale({ ...scale, labels: e.target.value })} className={inputClass} />
              </div>
            )}
            <p className="text-[13px] text-muted">
              Ratings are kept and shown with reviews; the analysis itself never uses them. Values off the scale are
              stored without a rating.
            </p>
          </fieldset>
        )}

        {fields.review_date && (
          <div className="mt-4 flex flex-col gap-1.5">
            <label htmlFor="date-format" className={labelClass}>
              Date format <span className="font-normal text-muted">(optional)</span>
            </label>
            <input
              id="date-format"
              value={dateFormat}
              onChange={(event) => setDateFormat(event.target.value)}
              placeholder="%m/%d/%Y"
              className={`${inputClass} max-w-[260px]`}
            />
            <span className="text-[13px] text-muted">
              Leave blank for ISO dates such as 2017-01-13. {sampleDate && <>A value from this file: &quot;{sampleDate}&quot;.</>}
            </span>
          </div>
        )}

        {otherColumns.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
            <span>Keep as metadata:</span>
            {otherColumns.map((column) => (
              <label key={column} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={metadata.includes(column)}
                  onChange={(event) =>
                    setMetadata((current) =>
                      event.target.checked ? [...current, column] : current.filter((name) => name !== column),
                    )
                  }
                />
                {column}
              </label>
            ))}
          </div>
        )}
      </section>

      <section className={`${card} p-5`}>
        <h2 className="mb-3 text-[17px] font-semibold">Preview</h2>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-sm">
            <thead>
              <tr className="text-left text-muted">
                {["Text", "Title", "Rating", "Date"].map((heading) => (
                  <th key={heading} className="border-b border-line p-2 font-medium">
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.map((row, index) => (
                <tr key={index} className="align-top">
                  <td className="border-b border-subtle p-2">{(row[fields.text] ?? "").slice(0, 160)}</td>
                  <td className="border-b border-subtle p-2">{fields.title ? row[fields.title] : ""}</td>
                  <td className="border-b border-subtle p-2">{fields.rating ? row[fields.rating] : ""}</td>
                  <td className="border-b border-subtle p-2 whitespace-nowrap">{fields.review_date ? row[fields.review_date] : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <ErrorNote error={problem ?? upload.error} />
      <div className="flex flex-wrap justify-end gap-3">
        <button type="button" onClick={onBack} className={buttonSecondary}>
          Back
        </button>
        <button type="button" onClick={submit} disabled={upload.isPending} className={buttonPrimary}>
          {upload.isPending ? "Uploading…" : `Upload ${formatNumber(parsed.rows.length)} reviews`}
        </button>
      </div>
    </div>
  );
}

function Done({ entityId, result }: { entityId: string; result: UploadResult }) {
  const { import: summary, analysis } = result;
  const analysisNote = analysis.run
    ? analysis.reused
      ? "The new reviews join the analysis already waiting for this entity."
      : "Analysis is queued. It usually takes a few minutes, and you can leave this page."
    : (analysis.message ?? "No analysis was needed.");
  return (
    <section className={`${card} flex flex-col gap-4 px-6 py-8`}>
      <h2 className="text-xl font-semibold">{formatNumber(summary.rows_inserted)} reviews added</h2>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-3">
        {[
          ["Rows received", summary.rows_received],
          ["Added", summary.rows_inserted],
          ["Duplicates skipped", summary.rows_skipped],
        ].map(([label, value]) => (
          <div key={label} className="rounded-lg bg-subtle px-3.5 py-3">
            <div className="text-[13px] text-muted">{label}</div>
            <div className="text-xl font-semibold">{formatNumber(Number(value))}</div>
          </div>
        ))}
      </div>
      <p>{analysisNote}</p>
      <div>
        <Link href={`/entities/${entityId}`} className={buttonPrimary}>
          View results
        </Link>
      </div>
    </section>
  );
}

export default function UploadPage() {
  const { entityId } = useParams<{ entityId: string }>();
  const entity = useEntity(entityId);
  const [chosen, setChosen] = useState<{ file: File; parsed: ParsedCsv } | null>(null);
  const [result, setResult] = useState<UploadResult | null>(null);
  const step = result ? 3 : chosen ? 2 : 1;

  if (entity.isPending) return <Page><Loading /></Page>;
  if (!entity.data) return <Page><ErrorNote error={entity.error} /></Page>;

  return (
    <Page width="max-w-[860px]">
      <BackLink href={`/entities/${entityId}`}>{entity.data.name}</BackLink>
      <div className="mt-2 mb-5">
        <PageTitle>Upload reviews</PageTitle>
      </div>
      {!entity.data.is_owner ? (
        <p className="text-muted">Only the owner of an entity can upload reviews to it.</p>
      ) : (
        <>
          <Steps current={step} />
          {step === 1 && <ChooseFile onParsed={(file, parsed) => setChosen({ file, parsed })} />}
          {step === 2 && chosen && (
            <MapColumns
              entityId={entityId}
              file={chosen.file}
              parsed={chosen.parsed}
              onBack={() => setChosen(null)}
              onUploaded={setResult}
            />
          )}
          {step === 3 && result && <Done entityId={entityId} result={result} />}
        </>
      )}
    </Page>
  );
}
