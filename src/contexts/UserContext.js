import React, { createContext, useState, useContext, useEffect } from 'react';
import axios from 'axios';
import api from '../config/api';

const UserContext = createContext();

export const UserProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sessionLoaded, setSessionLoaded] = useState(false);

  useEffect(() => {
    const fetchSession = async () => {
      try {
        const response = await axios.get(`${api.baseURL}/profile`, {
          withCredentials: api.withCredentials,
          headers: api.headers
        });
        setUser(response.data);
      } catch (error) {
        // Silently handle unauthenticated users
        if (error.response?.status === 403 || error.response?.status === 401) {
          setUser(null);
        } else {
          console.error('Error fetching session:', error);
        }
      } finally {
        setLoading(false);
        setSessionLoaded(true);
      }
    };

    fetchSession();
  }, []);

  const value = {
    user,
    setUser,
    loading,
    sessionLoaded
  };

  return (
    <UserContext.Provider value={value}>
      {children}
    </UserContext.Provider>
  );
};

export const useUser = () => {
  const context = useContext(UserContext);
  if (!context) {
    throw new Error('useUser must be used within a UserProvider');
  }
  return context;
}; 