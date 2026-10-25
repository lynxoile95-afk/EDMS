import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { AuthProvider, useAuth } from "@/lib/auth";
import { ProjectProvider } from "@/lib/project";
import { Layout } from "@/components/Layout";
import { Spinner } from "@/components/ui";
import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { MDLLibrary } from "@/pages/MDLLibrary";
import { UploadPage } from "@/pages/Upload";
import { RegistrationPage } from "@/pages/Registration";
import { ReviewQueue } from "@/pages/ReviewQueue";
import { ReviewDetail } from "@/pages/ReviewDetail";
import { Team } from "./pages/Team";
import { Transmittals } from "@/pages/Transmittals";
import { TransmittalDetail } from "@/pages/TransmittalDetail";
import { CommentList } from "@/pages/CommentList";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="در حال بررسی نشست…" />
      </div>
    );
  }
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;
  return <>{children}</>;
}

function GuestOnly({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (user) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ProjectProvider>
          <Routes>
            <Route
              path="/login"
              element={
                <GuestOnly>
                  <Login />
                </GuestOnly>
              }
            />
            <Route
              element={
                <RequireAuth>
                  <Layout />
                </RequireAuth>
              }
            >
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/mdl" element={<MDLLibrary />} />
              <Route path="/upload" element={<UploadPage />} />
              <Route path="/registration" element={<RegistrationPage />} />
              <Route path="/review" element={<ReviewQueue />} />
              <Route path="/review/:resultId" element={<ReviewDetail />} />
              <Route path="/team" element={<Team />} />
              <Route path="/transmittals" element={<Transmittals />} />
              <Route path="/transmittals/:transmittalId" element={<TransmittalDetail />} />
              <Route path="/documents/:revisionId/comments" element={<CommentList />} />
            </Route>
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </ProjectProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
