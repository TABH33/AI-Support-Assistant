import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthProvider'
import { SelectionProvider } from './context/SelectionContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { RequireRole } from './components/RequireRole'
import { Layout } from './components/Layout'
import Login from './pages/Login'
import Overview from './pages/Overview'
import RoutesPage from './pages/Routes'
import LiveTracking from './pages/LiveTracking'
import Drivers from './pages/Drivers'
import DriverDetail from './pages/DriverDetail'
import Alerts from './pages/Alerts'

/** A customer's fleet-management-free default landing page is `/routes`;
 * a support_agent's is `/overview`, unchanged from before this page was
 * scoped to support_agent only. */
function DefaultRoute() {
  const { user } = useAuth()
  return <Navigate to={user?.role === 'support_agent' ? '/overview' : '/routes'} replace />
}

function App() {
  return (
    <AuthProvider>
      <SelectionProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />

            <Route
              element={
                <ProtectedRoute>
                  <Layout />
                </ProtectedRoute>
              }
            >
              <Route index element={<DefaultRoute />} />
              <Route
                path="/overview"
                element={
                  <RequireRole role="support_agent" redirectTo="/routes">
                    <Overview />
                  </RequireRole>
                }
              />
              <Route
                path="/routes"
                element={
                  <RequireRole role="customer" redirectTo="/overview">
                    <RoutesPage />
                  </RequireRole>
                }
              />
              <Route path="/tracking" element={<LiveTracking />} />
              <Route
                path="/drivers"
                element={
                  <RequireRole role="support_agent" redirectTo="/routes">
                    <Drivers />
                  </RequireRole>
                }
              />
              <Route
                path="/drivers/:driverId"
                element={
                  <RequireRole role="support_agent" redirectTo="/routes">
                    <DriverDetail />
                  </RequireRole>
                }
              />
              <Route path="/alerts" element={<Alerts />} />
              <Route path="*" element={<DefaultRoute />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </SelectionProvider>
    </AuthProvider>
  )
}

export default App
