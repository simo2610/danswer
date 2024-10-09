"use client";

import { ApiKeyForm } from "./ApiKeyForm";
import { Modal } from "../Modal";
import { useRouter } from "next/navigation";
import { useProviderStatus } from "../chat_search/ProviderContext";
import { PopupSpec } from "../admin/connectors/Popup";

export const ApiKeyModal = ({
  hide,
  setPopup,
}: {
  hide: () => void;
  setPopup: (popup: PopupSpec) => void;
}) => {
  const router = useRouter();

  const {
    shouldShowConfigurationNeeded,
    providerOptions,
    refreshProviderInfo,
  } = useProviderStatus();

  if (!shouldShowConfigurationNeeded) {
    return null;
  }

  return (
    <Modal
      title="Set an API Key!"
      width="max-w-3xl w-full"
      onOutsideClick={() => hide()}
    >
      <div className="max-h-[75vh] overflow-y-auto flex flex-col">
        <div>
          <div className="mb-5 text-sm">
            Please provide an API Key below in order to start using
            Danswer – you can always change this later.
            <br />
            If you&apos;d rather look around first, you can
            <strong onClick={() => hide()} className="text-link cursor-pointer">
              {" "}
              skip this step
            </strong>
            .
          </div>

          <ApiKeyForm
            setPopup={setPopup}
            onSuccess={() => {
              router.refresh();
              refreshProviderInfo();
              hide();
            }}
            providerOptions={providerOptions}
          />
        </div>
      </div>
    </Modal>
  );
};
