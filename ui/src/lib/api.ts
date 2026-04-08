import { invoke } from "@tauri-apps/api/core";

export type PermissionStatus = "granted" | "denied" | "unknown";

export interface Permissions {
  screen_recording: PermissionStatus;
  accessibility: PermissionStatus;
  microphone: PermissionStatus;
}

export interface SidecarHealth {
  ok: boolean;
  version: string | null;
}

export const api = {
  getPermissions: () => invoke<Permissions>("get_permissions"),
  pingSidecar: () => invoke<SidecarHealth>("ping_sidecar"),
};
