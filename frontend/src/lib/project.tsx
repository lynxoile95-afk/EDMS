import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import { useAuth } from "./auth";
import type { Project } from "./types";

const STORAGE_KEY = "edms.project_id";

interface ProjectState {
  projects: Project[];
  project: Project | null;
  loading: boolean;
  select: (id: string) => void;
  reload: () => Promise<void>;
  isDcc: boolean;
}

const ProjectContext = createContext<ProjectState | undefined>(undefined);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(() => localStorage.getItem(STORAGE_KEY));
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!user) {
      setProjects([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const list = await api.get<Project[]>("/projects");
      setProjects(list);
      const stored = localStorage.getItem(STORAGE_KEY);
      // Project data must never silently land in the first project returned by the
      // API. A user explicitly selects the project from the project screen/header.
      const valid = stored && list.some((item) => item.id === stored) ? stored : null;
      setProjectId(valid);
      if (valid) localStorage.setItem(STORAGE_KEY, valid);
      else localStorage.removeItem(STORAGE_KEY);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    void load();
  }, [load]);

  const select = useCallback((id: string) => {
    if (!id) {
      localStorage.removeItem(STORAGE_KEY);
      setProjectId(null);
      return;
    }
    localStorage.setItem(STORAGE_KEY, id);
    setProjectId(id);
  }, []);

  const project = projects.find((item) => item.id === projectId) ?? null;
  const isDcc = Boolean(user?.is_superuser) || (project?.member_role ?? "") === "dcc";

  const value = useMemo(
    () => ({ projects, project, loading, select, reload: load, isDcc }),
    [projects, project, loading, select, load, isDcc],
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProject(): ProjectState {
  const context = useContext(ProjectContext);
  if (!context) throw new Error("useProject must be used inside <ProjectProvider>");
  return context;
}
