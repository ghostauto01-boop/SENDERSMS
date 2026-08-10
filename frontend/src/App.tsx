import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import MainLayout from "./layouts/MainLayout";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import ContactsPage from "./pages/ContactsPage";
import ListsPage from "./pages/ListsPage";
import CampaignsPage from "./pages/CampaignsPage";
import SequencesPage from "./pages/SequencesPage";
import InboxPage from "./pages/InboxPage";
import FollowUpsPage from "./pages/FollowUpsPage";
import TemplatesPage from "./pages/TemplatesPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import SettingsPage from "./pages/SettingsPage";
import SendPage from "./pages/SendPage";

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
    <Routes>
      <Route path="/login" element={<LoginPage />} />
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
        <Route path="/inbox" element={<InboxPage />} />
        <Route path="/follow-ups" element={<FollowUpsPage />} />
        <Route path="/templates" element={<TemplatesPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/send" element={<SendPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  );
}
