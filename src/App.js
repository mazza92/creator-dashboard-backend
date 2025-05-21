import React, { useEffect, useState } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { UserProvider, useUser } from './contexts/UserContext';
import axios from 'axios';
import api from './config/api';

// ... other imports ...

const AppContent = () => {
  const { user, loading, sessionLoaded } = useUser();
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchSession = async () => {
      try {
        await axios.get(`${api.baseURL}/profile`, {
          withCredentials: api.withCredentials,
          headers: api.headers
        });
      } catch (error) {
        // Only log errors that aren't authentication-related
        if (error.response?.status !== 403 && error.response?.status !== 401) {
          console.error('Error fetching session:', error);
          setError(error);
        }
      }
    };

    fetchSession();
  }, []);

  if (loading) {
    return <div>Loading...</div>;
  }

  // Handle any non-auth errors
  if (error) {
    return <div>Something went wrong. Please try again later.</div>;
  }

  return (
    <Routes>
      {/* Public routes */}
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      
      {/* Protected routes */}
      <Route
        path="/creator/*"
        element={
          user?.user_role === 'creator' ? (
            <CreatorApp />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
      <Route
        path="/brand/*"
        element={
          user?.user_role === 'brand' ? (
            <BrandApp />
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />
    </Routes>
  );
};

const App = () => {
  return (
    <Router>
      <UserProvider>
        <AppContent />
      </UserProvider>
    </Router>
  );
};

export default App; 