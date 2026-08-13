import { Suspense, lazy } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import MainLayout from "./layouts/MainLayout";
import LoginPage from "./pages/LoginPage";

// Route-level code splitting: each page ships as its own chunk so the initial
// load (the login screen) no longer pulls in the entire application.
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const ContactsPage = lazy(() => import("./pages/ContactsPage"));
const ListsPage = lazy(() => import("./pages/ListsPage"));
const CampaignsPage = lazy(() => import("./pages/CampaignsPage"));
const SequencesPage = lazy(() => import("./pages/SequencesPage"));
const InboxPage = lazy(() => import("./pages/InboxPage"));
const FollowUpsPage = lazy(() => import("./pages/FollowUpsPage"));
const TemplatesPage = lazy(() => import("./pages/TemplatesPage"));
const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const SendPage = lazy(() => import("./pages/SendPage"));
const AutoReplyPage = lazy(() => import("./pages/AutoReplyPage"));

function PageSpinner() {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
    </div>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Suspense fallback={<PageSpinner />}>
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* The inbox is a full-screen WhatsApp-style app of its own, so it
          renders outside the dashboard shell (no sidebar / top header). */}
      <Route
        path="/inbox"
        element={
          <ProtectedRoute>
            <InboxPage />
          </ProtectedRoute>
        }
      />
      <Route
        element={
          <ProtectedRoute>
            <MainLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/contacts" element={<ContactsPage />} />
        <Route path="/lists" element={<ListsPage />} />
        <Route path="/campaigns" element={<CampaignsPage />} />
        <Route path="/sequences" element={<SequencesPage />} />
        <Route path="/follow-ups" element={<FollowUpsPage />} />
        <Route path="/templates" element={<TemplatesPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/send" element={<SendPage />} />
        <Route path="/auto-reply" element={<AutoReplyPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
