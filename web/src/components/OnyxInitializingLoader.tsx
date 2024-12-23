import { Logo } from "./logo/Logo";
import { useContext } from "react";
import { SettingsContext } from "./settings/SettingsProvider";

export function OnyxInitializingLoader() {
  const settings = useContext(SettingsContext);

  return (
    <div className="mx-auto my-auto animate-pulse">
      <Logo height={96} width={96} className="mx-auto mb-3" />
      <p className="text-lg font-bold">
        Initializing{" "}
        {settings?.enterpriseSettings?.application_name ?? "Scientifica Answer"}
      </p>
    </div>
  );
}