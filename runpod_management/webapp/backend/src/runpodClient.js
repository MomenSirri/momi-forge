import axios from "axios";
import { config } from "./config.js";

const GRAPHQL_URL = "https://api.runpod.io/graphql";
const REST_URL_BASE = "https://api.runpod.ai/v2";

const ensureApiKey = () => {
  if (!config.runpodApiKey) {
    throw new Error("RUNPOD_API_KEY is missing. Add it in .env.");
  }
};

const getHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${config.runpodApiKey}`
});

const graphQLRequest = async (query, variables = {}) => {
  ensureApiKey();

  const response = await axios.post(
    GRAPHQL_URL,
    { query, variables },
    {
      params: { api_key: config.runpodApiKey },
      headers: getHeaders(),
      timeout: 15_000
    }
  );

  if (response.data?.errors?.length) {
    throw new Error(response.data.errors[0]?.message ?? "RunPod GraphQL error");
  }

  return response.data?.data;
};

export const getEndpoints = async () => {
  const query = `
    query {
      myself {
        endpoints {
          id
          name
          workersMin
          workersMax
          idleTimeout
          gpuIds
          templateId
          locations
          networkVolumeId
          scalerType
          scalerValue
        }
      }
    }
  `;

  const data = await graphQLRequest(query);
  return data?.myself?.endpoints ?? [];
};

export const getEndpointHealth = async (endpointId) => {
  ensureApiKey();

  const response = await axios.get(`${REST_URL_BASE}/${endpointId}/health`, {
    headers: getHeaders(),
    timeout: 10_000
  });

  return response.data;
};

export const updateEndpointConfig = async (endpointData, minWorkers, maxWorkers, idleTimeout) => {
  const input = {
    id: endpointData.id,
    name: endpointData.name,
    templateId: endpointData.templateId,
    gpuIds: endpointData.gpuIds,
    workersMin: Number(minWorkers),
    workersMax: Number(maxWorkers)
  };

  if (idleTimeout !== undefined && idleTimeout !== null) {
    input.idleTimeout = Number(idleTimeout);
  } else if (endpointData.idleTimeout !== undefined && endpointData.idleTimeout !== null) {
    input.idleTimeout = Number(endpointData.idleTimeout);
  }

  for (const optionalKey of ["locations", "networkVolumeId", "scalerType", "scalerValue"]) {
    if (endpointData[optionalKey] !== undefined && endpointData[optionalKey] !== null) {
      input[optionalKey] = endpointData[optionalKey];
    }
  }

  const mutation = `
    mutation saveEndpoint($input: EndpointInput!) {
      saveEndpoint(input: $input) {
        id
        name
        workersMin
        workersMax
        idleTimeout
      }
    }
  `;

  const data = await graphQLRequest(mutation, { input });
  return data?.saveEndpoint;
};
