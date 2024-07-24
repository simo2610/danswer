import { User } from "@/lib/types";
import { TbLayoutSidebarLeftExpand } from "react-icons/tb";
import { UserDropdown } from "../UserDropdown";
import { FiShare2, FiSidebar } from "react-icons/fi";
import { SetStateAction, useContext, useEffect } from "react";
import { Logo } from "../Logo";
import { ChatIcon, NewChatIcon, PlusCircleIcon } from "../icons/icons";
import { NEXT_PUBLIC_NEW_CHAT_DIRECTS_TO_SAME_PERSONA } from "@/lib/constants";
import { ChatSession } from "@/app/chat/interfaces";
import { HeaderTitle } from "../header/Header";
import { Tooltip } from "../tooltip/Tooltip";
import KeyboardSymbol from "@/lib/browserUtilities";
import Link from "next/link";
import { SettingsContext } from "../settings/SettingsProvider";

export default function FunctionalHeader({
  showSidebar,
  user,
  page,
  currentChatSession,
  setSharingModalVisible,
}: {
  page: "search" | "chat" | "assistants";
  showSidebar: boolean;
  user: User | null;
  currentChatSession?: ChatSession | null | undefined;
  setSharingModalVisible?: (value: SetStateAction<boolean>) => void;
}) {
  const combinedSettings = useContext(SettingsContext);
  const settings = combinedSettings?.settings;
  const enterpriseSettings = combinedSettings?.enterpriseSettings;

  const commandSymbol = KeyboardSymbol();

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey) {
        switch (event.key.toLowerCase()) {
          case "u":
            event.preventDefault();
            window.open(
              `/${page}` +
                (NEXT_PUBLIC_NEW_CHAT_DIRECTS_TO_SAME_PERSONA &&
                currentChatSession
                  ? `?assistantId=${currentChatSession.persona_id}`
                  : ""),
              "_self"
            );
            break;
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, []);
  return (
    <div className="pb-6 left-0 sticky top-0 z-10 w-full relative flex">
      <div className="mt-2 mx-4 text-text-700 flex w-full">
        <div className="absolute z-[100] my-auto flex items-center text-xl font-bold">
          <div className="pt-[2px] mb-auto">
            <FiSidebar size={20} />
          </div>
          <div className="break-words inline-block w-fit ml-2 text-text-700 text-xl">
            <div className="max-w-[200px]">
              {enterpriseSettings && enterpriseSettings.application_name ? (
                <HeaderTitle>{enterpriseSettings.application_name}</HeaderTitle>
              ) : (
                <HeaderTitle>Scientifica </HeaderTitle>
              )}
            </div>
          </div>

          {page == "chat" && (
            <Tooltip delayDuration={1000} content={`${commandSymbol}U`}>
              <Link
                className="mb-auto pt-[2px]"
                href={
                  `/${page}` +
                  (NEXT_PUBLIC_NEW_CHAT_DIRECTS_TO_SAME_PERSONA &&
                  currentChatSession
                    ? `?assistantId=${currentChatSession.persona_id}`
                    : "")
                }
              >
                <div className="cursor-pointer ml-2 flex-none text-text-700 hover:text-text-600 transition-colors duration-300">
                  <NewChatIcon size={20} className="" />
                </div>
              </Link>
            </Tooltip>
          )}
        </div>

        <div className="ml-auto my-auto flex gap-x-2">
          {setSharingModalVisible && (
            <div
              onClick={() => setSharingModalVisible(true)}
              className="my-auto rounded cursor-pointer hover:bg-hover-light"
            >
              <FiShare2 size="18" />
            </div>
          )}

          <div className="flex my-auto">
            <UserDropdown user={user} />
          </div>
        </div>
      </div>

      {page != "assistants" && (
        <div className="h-24 left-0 absolute top-0 z-10 w-full bg-gradient-to-b via-50% z-[-1] from-background via-background to-background/10 flex" />
      )}
    </div>
  );
}
