import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { fetchAgentCatalog, fetchManagementAgentCatalog } from '../brainApi';
import type { AgentCapabilityCard } from '../brainTypes';
import { webApplications, catalogInventory } from './webEntryPolicy';

export type CatalogLoader = (signal?: AbortSignal) => Promise<AgentCapabilityCard[]>;
interface CatalogState { catalog: AgentCapabilityCard[]; applications: AgentCapabilityCard[]; status: 'loading' | 'ready' | 'error'; retry: () => void }
const WebCatalogContext = createContext<CatalogState | null>(null);

// The keyed session clears grants synchronously on identity changes, even before effects run.
export function WebCatalogProvider({ identityKey, loadCatalog, management = false, children }: {
  identityKey: string | null; loadCatalog?: CatalogLoader; management?: boolean; children: ReactNode;
}) {
  return <CatalogSession key={JSON.stringify([identityKey, management])} identityKey={identityKey} loadCatalog={loadCatalog ?? (management ? fetchManagementAgentCatalog : fetchAgentCatalog)}>{children}</CatalogSession>;
}
function CatalogSession({ identityKey, loadCatalog, children }: { identityKey: string | null; loadCatalog: CatalogLoader; children: ReactNode }) {
  const [state, setState] = useState<Omit<CatalogState, 'retry'>>({catalog: [], applications: [], status: identityKey ? 'loading' : 'ready'});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!identityKey) return;
    const controller = new AbortController();
    setState({catalog: [], applications: [], status: 'loading'});
    void loadCatalog(controller.signal).then(cards => {
      if (!controller.signal.aborted) setState({catalog: catalogInventory(cards), applications: webApplications(catalogInventory(cards)), status: 'ready'});
    }).catch(() => {
      if (!controller.signal.aborted) setState({catalog: [], applications: [], status: 'error'});
    });
    return () => controller.abort();
  }, [identityKey, loadCatalog, attempt]);
  return <WebCatalogContext.Provider value={{...state, retry: () => setAttempt(value => value + 1)}}>{children}</WebCatalogContext.Provider>;
}
export function useWebCatalog() { return useContext(WebCatalogContext); }
