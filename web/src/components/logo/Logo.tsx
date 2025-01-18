"use client";

import { useContext } from "react";
import { SettingsContext } from "../settings/SettingsProvider";
import Image from "next/image";

export function Logo({
  height,
  width,
  className,
}: {
  height?: number;
  width?: number;
  className?: string;
}) {
  const settings = useContext(SettingsContext);

  height = height || 32;
  width = width || 30;

  if (
    !settings ||
    !settings.enterpriseSettings ||
    !settings.enterpriseSettings.use_custom_logo
  ) {
    return (
      <div style={{ height, width }} className={className}>
        <Image src="/logo.jpeg" alt="Logo" width={width} height={height} />
      </div>
    );
  }

  return (
    <div
      style={{ height, width }}
      className={`flex-none relative ${className}`}
    >
      {/* TODO: figure out how to use Next Image here */}
      <img
        src="/api/enterprise-settings/logo"
        alt="Logo"
        style={{ objectFit: "contain", height, width }}
      />
    </div>
  );
}

export function LogoType() {
  return (
    <Image
      priority
      className="max-h-8 w-full mr-auto "
      src="/logotype.png"
      alt="Logo"
      width={2640}
      height={733}
      style={{ objectFit: "contain", width: "100%", height: "100%" }}
      loading="eager"
      unoptimized={true}
    />
  );
}
