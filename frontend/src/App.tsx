import { Suspense, lazy } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { ChannelProvider } from "./hooks/useChannel";
import { ThemeProvider } from "./hooks/useTheme";
import MainLayout from "./layouts/MainLayout";
import LoginPage from "./pages/LoginPage";
import DbStatusBanner from "./components/DbStatusBanner";
import ServerWakeBanner from "./components/ServerWakeBanner";
import useServerKeepAlive from "./hooks/useServerKeepAlive";

// Route-level code splitting: each page ships as its own chunk so the initial
// load (the login screen) no longer pulls in the entire application.
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const OverviewPage = lazy(() => import("./pages/OverviewPage"));
const ContactsPage = lazy(() => import("./pages/ContactsPage"));
const ValidatorPage = lazy(() => import("./pages/ValidatorPage"));
const ListsPage = lazy(() => import("./pages/ListsPage"));
const SMSManagerPage = lazy(() => import("./pages/SMSManagerPage"));
const EmailManagerPage = lazy(() => import("./pages/EmailManagerPage"));
const EmailInboxPage = lazy(() => import("./pages/EmailInboxPage"));
const SequencesPage = lazy(() => import("./pages/SequencesPage"));
const InboxPage = lazy(() => import("./pages/InboxPage"));
const ConversationsPage = lazy(() => import("./pages/ConversationsPage"));
const OpportunityPage = lazy(() => import("./pages/OpportunityPage"));
const FollowUpsPage = lazy(() => import("./pages/FollowUpsPage"));
const TemplatesPage = lazy(() => import("./pages/TemplatesPage"));
const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const SendPage = lazy(() => import("./pages/SendPage"));
const DraftsPage = lazy(() => import("./pages/DraftsPage"));
const AutoReplyPage = lazy(() => import("./pages/AutoReplyPage"));
const AutomationsPage = lazy(() => import("./pages/AutomationsPage"));
const VariablesPage = lazy(() => import("./pages/VariablesPage"));
const CampaignFollowUpsPage = lazy(() => import("./pages/CampaignFollowUpsPage"));
const CalendarPage = lazy(() => import("./pages/CalendarPage"));
const AudiencesPage = lazy(() => import("./pages/AudiencesPage"));
const PhonePage = lazy(() => import("./pages/PhonePage"));
const NotificationsPage = lazy(() => import("./pages/NotificationsPage"));
const InvoicePage = lazy(() => import("./pages/InvoicePage"));
const PublicInvoicePage = lazy(() => import("./pages/PublicInvoicePage"));
const SetupPage = lazy(() => import("./pages/SetupPage"));
const AIAgentPage = lazy(() => import("./pages/AIAgentPage")); // NEW AI AGENT TAB

function PageSpinner() {
  return (
    <div className="flex items-center justify-center py-20">
      <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary-600" />
    </div>
  );
}

export default function App() {
  const { user } = useAuth();
  useServerKeepAlive();

  if (!user) {
    return (
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    );
  }

  return (
    <AuthProvider>
      <ChannelProvider>
        <ThemeProvider>
          <MainLayout>
            <Suspense fallback={<PageSpinner />}>
              <Routes>
                <Route path="/" element={<Navigate to="/overview" replace />} />
                <Route path="/overview" element={<OverviewPage />} />
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/contacts" element={<ContactsPage />} />
                <Route path="/validator" element={<ValidatorPage />} />
                <Route path="/lists" element={<ListsPage />} />
                <Route path="/sms-manager" element={<SMSManagerPage />} />
                <Route path="/email-manager" element={<EmailManagerPage />} />
                <Route path="/email-inbox" element={<EmailInboxPage />} />
                <Route path="/sequences" element={<SequencesPage />} />
                <Route path="/inbox" element={<InboxPage />} />
                <Route path="/conversations" element={<ConversationsPage />} />
                <Route path="/opportunity" element={<OpportunityPage />} />
                <Route path="/follow-ups" element={<FollowUpsPage />} />
                <Route path="/templates" element={<TemplatesPage />} />
                <Route path="/analytics" element={<AnalyticsPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/send" element={<SendPage />} />
                <Route path="/drafts" element={<DraftsPage />} />
                <Route path="/auto-reply" element={<AutoReplyPage />} />
                <Route path="/automations" element={<AutomationsPage />} />
                <Route path="/variables" element={<VariablesPage />} />
                <Route path="/campaign-follow-ups" element={<CampaignFollowUpsPage />} />
                <Route path="/calendar" element={<CalendarPage />} />
                <Route path="/audiences" element={<AudiencesPage />} />
                <Route path="/phone" element={<PhonePage />} />
                <Route path="/notifications" element={<NotificationsPage />} />
                <Route path="/invoices" element={<InvoicePage />} />
                <Route path="/public-invoice" element={<PublicInvoicePage />} />
                <Route path="/setup" element={<SetupPage />} />
                <Route path="/ai-agent" element={<AIAgentPage />} /> {/* NEW ROUTE */}
                <Route path="*" element={<Navigate to="/overview" replace />} />
              </Routes>
            </Suspense>
            <DbStatusBanner />
            <ServerWakeBanner />
          </MainLayout>
        </ThemeProvider>
      </ChannelProvider>
    </AuthProvider>
  );
}
