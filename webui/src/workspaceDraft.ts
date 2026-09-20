import { createContext, useContext } from 'react';

// A durable business transition, not a submit click, may release draft protection.
export const WorkspaceDraftCommit = createContext<() => void>(() => undefined);
export function useWorkspaceDraftCommit() { return useContext(WorkspaceDraftCommit); }
