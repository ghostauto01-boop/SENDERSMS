import { Suspense, lazy } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { ChannelProvider } from "./hooks/useChannel";
import MainLayout from "./layouts/MainLayout";
import LoginPage from "./pages/LoginPage";
import DbStatusBanner from "./components/DbStatusBanner";

// Route-level code splitting: each page ships as its own chunk so the initial
// load (the login screen) no longer pulls in the entire application.
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const OverviewPage = lazy(() => import("./pages/OverviewPage"));
const ContactsPage = lazy(() => import("./pages/ContactsPage"));
const ValidatorPage = lazy(() => import("./pages/ValidatorPage"));
const ListsPage = lazy(() => import("./pages/ListsPage"));
const CampaignsPage = lazy(() => import("./pages/CampaignsPage"));
const SMSManagerPage = lazy(() => import("./pages/SMSManagerPage"));
const EmailManagerPage = lazy(() => import("./pages/EmailManagerPage"));
const EmailInboxPage = lazy(() => import("./pages/EmailInboxPage"));
const SequencesPage = lazy(() => import("./pages/SequencesPage"));
const InboxPage = lazy(() => import("./pages/InboxPage"));
const FollowUpsPage = lazy(() => import("./pages/FollowUpsPage"));
const TemplatesPage = lazy(() => import("./pages/TemplatesPage"));
const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const SendPage = lazy(() => import("./pages/SendPage"));
const AutoReplyPage = lazy(() => import("./pages/AutoReplyPage"));
const AutomationsPage = lazy(() => import("./pages/AutomationsPage"));
const VariablesPage = lazy(() => import("./pages/VariablesPage"));
const CampaignFollowUpsPage = lazy(() => import("./pages/CampaignFollowUpsPage"));
const CalendarPage = lazy(() => import("./pages/CalendarPage"));
const AudiencesPage = lazy(() => import("./pages/AudiencesPage"));
const PhonePage = lazy(() => import("./pages/PhonePage"));
const NotificationsPage = lazy(() => import("./pages/NotificationsPage"));
const SetupPage = lazy(() => import("./pages/SetupPage"));

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

  // No password anywhere: /login is a single "Log in as admin" button.
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
        <Route path="/overview" element={<OverviewPage />} />
        <Route path="/contacts" element={<ContactsPage />} />
        <Route path="/validator" element={<ValidatorPage />} />
        <Route path="/phone" element={<PhonePage />} />
        <Route path="/lists" element={<ListsPage />} />
        <Route path="/audiences" element={<AudiencesPage />} />
        <Route path="/campaigns" element={<CampaignsPage />} />
        <Route path="/sms-manager" element={<SMSManagerPage />} />
        <Route path="/email-manager" element={<EmailManagerPage />} />
        <Route path="/email-inbox" element={<EmailInboxPage />} />
        <Route path="/sequences" element={<SequencesPage />} />
        <Route path="/follow-ups" element={<FollowUpsPage />} />
        <Route path="/templates" element={<TemplatesPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/send" element={<SendPage />} />
        <Route path="/auto-reply" element={<AutoReplyPage />} />
        <Route path="/automations" element={<AutomationsPage />} />
        <Route path="/variables" element={<VariablesPage />} />
        <Route path="/campaign-follow-ups" element={<CampaignFollowUpsPage />} />
        <Route path="/calendar" element={<CalendarPage />} />
        {/* Reached from the bell's "View all notifications" and from any
            notification deep link. Without this route it fell through to the
            catch-all and every one of those links opened the Dashboard. */}
        <Route path="/notifications" element={<NotificationsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <AuthProvider>
      {/* SMS | Email is an app-wide choice: every channel-aware page reads it
          from here instead of each page growing its own switch. */}
      <ChannelProvider>
        {/* One explained notice when the database itself is down, above every
            page — instead of every widget failing on its own. */}
        <DbStatusBanner />
        <AppRoutes />
      </ChannelProvider>
    </AuthProvider>
  );
}
