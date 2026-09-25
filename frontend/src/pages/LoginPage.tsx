import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import toast from "react-hot-toast";
import { LogIn } from "lucide-react";
import BrandMark from "../components/BrandMark";

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const { loginAsAdmin, isAuthenticated, loading: authLoading } = useAuth();
  const navigate = useNavigate();

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600" />
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  const handleEnter = async () => {
    setLoading(true);
    const success = await loginAsAdmin();
    setLoading(false);
    if (success) {
      toast.success("Signed in as admin");
      navigate("/dashboard");
    } else {
      toast.error("Could not sign in — is the server reachable?");
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900 px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <BrandMark className="w-16 h-16 mx-auto mb-4" />
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">
            SMS SENDER
          </h1>
          <p className="text-gray-500 dark:text-gray-400 mt-1">
            Nigerian SMS Outreach CRM
          </p>
        </div>

        <div className="card p-6">
          <button
            type="button"
            onClick={handleEnter}
            disabled={loading}
            className="btn-primary w-full flex items-center justify-center gap-2"
          >
            {loading ? (
              <span className="flex items-center gap-2">
                <span className="animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full" />
                Signing in...
              </span>
            ) : (
              <>
                <LogIn size={18} />
                Log in as admin
              </>
            )}
          </button>
          <p className="text-xs text-gray-400 mt-3 text-center">
            No password. One tap and you are in.
          </p>
        </div>
      </div>
    </div>
  );
}
