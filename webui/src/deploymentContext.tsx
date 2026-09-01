import { createContext, useContext, type ReactNode } from "react";

import type { DeploymentInfo } from "./types";


interface DeploymentContextValue {
  deployment: DeploymentInfo | null;
  resolved: boolean;
}


const DeploymentContext = createContext<DeploymentContextValue>({ deployment: null, resolved: true });


export function DeploymentProvider({
  children,
  deployment,
  resolved,
}: DeploymentContextValue & { children: ReactNode }) {
  return <DeploymentContext.Provider value={{ deployment, resolved }}>{children}</DeploymentContext.Provider>;
}


export function useDeploymentContext(): DeploymentContextValue {
  return useContext(DeploymentContext);
}
