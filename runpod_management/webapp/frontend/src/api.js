import axios from "axios";

const api = axios.create({
  baseURL: "/api",
  timeout: 20_000
});

const normalizeError = (error) =>
  error?.response?.data?.error ?? error?.message ?? "Unexpected network error.";

export const fetchEndpoints = async () => {
  try {
    const response = await api.get("/endpoints");
    return response.data?.endpoints ?? [];
  } catch (error) {
    throw new Error(normalizeError(error));
  }
};

export const fetchSession = async () => {
  try {
    const response = await api.get("/session");
    return response.data;
  } catch (error) {
    throw new Error(normalizeError(error));
  }
};

export const fetchDashboard = async (endpointId) => {
  try {
    const response = await api.get(`/endpoint/${endpointId}/dashboard`);
    return response.data;
  } catch (error) {
    throw new Error(normalizeError(error));
  }
};

export const updateWorkers = async (endpointId, payload) => {
  try {
    const response = await api.post(`/endpoint/${endpointId}/workers`, payload);
    return response.data;
  } catch (error) {
    throw new Error(normalizeError(error));
  }
};

export const fetchAutomation = async (endpointId) => {
  try {
    const response = await api.get(`/endpoint/${endpointId}/automation`);
    return response.data?.automation;
  } catch (error) {
    throw new Error(normalizeError(error));
  }
};

export const saveAutomation = async (endpointId, payload) => {
  try {
    const response = await api.post(`/endpoint/${endpointId}/automation`, payload);
    return response.data?.automation;
  } catch (error) {
    throw new Error(normalizeError(error));
  }
};

export const fetchLive = async (endpointId) => {
  try {
    const response = await api.get(`/endpoint/${endpointId}/live`);
    return response.data;
  } catch (error) {
    throw new Error(normalizeError(error));
  }
};
