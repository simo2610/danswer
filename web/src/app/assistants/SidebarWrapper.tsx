"use client";

import { HistorySidebar } from "@/app/chat/sessionSidebar/HistorySidebar";

import { ChatSession } from "@/app/chat/interfaces";
import { Folder } from "@/app/chat/folders/interfaces";
import { User } from "@/lib/types";
import Cookies from "js-cookie";
import { SIDEBAR_TOGGLED_COOKIE_NAME } from "@/components/resizable/constants";
import { ReactNode, useContext, useEffect, useRef, useState } from "react";
import { useSidebarVisibility } from "@/components/chat_search/hooks";
import FunctionalHeader from "@/components/chat_search/Header";
import { useRouter } from "next/navigation";
import { pageType } from "../chat/sessionSidebar/types";
import FixedLogo from "../chat/shared_chat_search/FixedLogo";
import { SettingsContext } from "@/components/settings/SettingsProvider";

interface SidebarWrapperProps<T extends object> {
  chatSessions?: ChatSession[];
  folders?: Folder[];
  initiallyToggled: boolean;
  openedFolders?: { [key: number]: boolean };
  content: (props: T) => ReactNode;
  headerProps: {
    page: pageType;
    user: User | null;
  };
  contentProps: T;
  page: pageType;
  size?: "sm" | "lg";
}

export default function SidebarWrapper<T extends object>({
  chatSessions,
  initiallyToggled,
  folders,
  openedFolders,
  page,
  headerProps,
  contentProps,
  content,
  size = "sm",
}: SidebarWrapperProps<T>) {
  const [toggledSidebar, setToggledSidebar] = useState(initiallyToggled);

  const [showDocSidebar, setShowDocSidebar] = useState(false); // State to track if sidebar is open

  const toggleSidebar = () => {
    Cookies.set(
      SIDEBAR_TOGGLED_COOKIE_NAME,
      String(!toggledSidebar).toLocaleLowerCase()
    ),
      {
        path: "/",
      };
    setToggledSidebar((toggledSidebar) => !toggledSidebar);
  };

  const sidebarElementRef = useRef<HTMLDivElement>(null);

  const settings = useContext(SettingsContext);
  useSidebarVisibility({
    toggledSidebar,
    sidebarElementRef,
    showDocSidebar,
    setShowDocSidebar,
    mobile: settings?.isMobile,
  });

  const innerSidebarElementRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey) {
        switch (event.key.toLowerCase()) {
          case "e":
            event.preventDefault();
            toggleSidebar();
            break;
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [router]);

  return (
    <div className="flex relative overflow-x-hidden overscroll-contain flex-col w-full h-screen">
      <div
        ref={sidebarElementRef}
        className={`
            flex-none
            fixed
            left-0
            z-30
            bg-background-100
            h-screen
            transition-all
            bg-opacity-80
            duration-300
            ease-in-out
            ${
              showDocSidebar || toggledSidebar
                ? "opacity-100 w-[250px] translate-x-0"
                : "opacity-0 w-[200px] pointer-events-none -translate-x-10"
            }`}
      >
        <div className="w-full relative">
          <HistorySidebar
            page={page}
            ref={innerSidebarElementRef}
            toggleSidebar={toggleSidebar}
            toggled={toggledSidebar}
            existingChats={chatSessions}
            currentChatSession={null}
            folders={folders}
            openedFolders={openedFolders}
          />
        </div>
      </div>

      <div className="absolute h-svh left-0 w-full top-0">
        <FunctionalHeader
          toggleSidebar={toggleSidebar}
          page="assistants"
          user={headerProps.user}
        />
        <div className="w-full flex">
          <div
            style={{ transition: "width 0.30s ease-out" }}
            className={`flex-none
                      overflow-y-hidden
                      bg-background-100
                      h-full
                      transition-all
                      bg-opacity-80
                      duration-300 
                      ease-in-out
                      ${toggledSidebar ? "w-[250px]" : "w-[0px]"}`}
          />

          <div
            className={`mt-4 w-full ${size == "lg" ? "max-w-4xl" : "max-w-3xl"} mx-auto`}
          >
            {content(contentProps)}
          </div>
        </div>
      </div>
      <FixedLogo />
    </div>
  );
}
