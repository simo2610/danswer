import React from "react";
import NumberInput from "./ConnectorInput/NumberInput";
import { TextFormField } from "@/components/Field";
import { TrashIcon } from "@/components/icons/icons";

export default function AdvancedFormPage() {
  return (
    <div className="py-4 flex flex-col gap-y-6 rounded-lg max-w-2xl mx-auto">
      <h2 className="text-2xl font-bold mb-4 text-text-800">
        Advanced Configuration
      </h2>

      <NumberInput
        description={`
          Checks all documents against the source to delete those that no longer exist.
          Note: This process checks every document, so be cautious when increasing frequency.
          Default is 720 hours (30 days). Decimal hours are supported (e.g., 0.1 hours = 6 minutes).
          Enter 0 to disable pruning for this connector.
        `}
        label="Prune Frequency (hours)"
        name="pruneFreq"
      />

      <NumberInput
        description="This is how frequently we pull new documents from the source (in minutes). If you input 0, we will never pull new documents for this connector."
        label="Refresh Frequency (minutes)"
        name="refreshFreq"
      />

      <TextFormField
        type="date"
        subtext="Documents prior to this date will not be pulled in"
        optional
        label="Indexing Start Date"
        name="indexingStart"
      />
      <div className="mt-4 flex w-full mx-auto max-w-2xl justify-start">
        <button className="flex gap-x-1 bg-red-500 hover:bg-red-500/80 items-center text-white py-2.5 px-3.5 text-sm font-regular rounded ">
          <TrashIcon size={20} className="text-white" />
          <div className="w-full items-center gap-x-2 flex">Reset</div>
        </button>
      </div>
    </div>
  );
}
