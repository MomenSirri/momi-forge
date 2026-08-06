import requests
from config import RUNPOD_API_KEY

GRAPHQL_URL = "https://api.runpod.io/graphql"
REST_URL_BASE = "https://api.runpod.ai/v2"


def get_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RUNPOD_API_KEY}"
    }


def get_params():
    return {"api_key": RUNPOD_API_KEY}


def get_endpoints():
    if not RUNPOD_API_KEY:
        return None, "RUNPOD_API_KEY is not set in environment."

    query = """
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
    """
    try:
        response = requests.post(GRAPHQL_URL, params=get_params(), json={"query": query}, headers=get_headers(),
                                 timeout=15)
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            return None, f"GraphQL Error: {data['errors'][0].get('message', 'Unknown Error')}"
        return data.get("data", {}).get("myself", {}).get("endpoints", []), None
    except Exception as e:
        return None, f"Error: {str(e)}"


def get_endpoint_health(endpoint_id):
    if not RUNPOD_API_KEY:
        return None, "RUNPOD_API_KEY is not set."

    url = f"{REST_URL_BASE}/{endpoint_id}/health"
    try:
        response = requests.get(url, headers=get_headers(), timeout=10)
        response.raise_for_status()
        return response.json(), None
    except Exception as e:
        return None, f"Health API Error: {str(e)}"


# MODIFIED: Renamed to update_endpoint_config and added idle_timeout
def update_endpoint_config(ep_data, min_workers, max_workers, idle_timeout=None):
    if not RUNPOD_API_KEY:
        return None, "RUNPOD_API_KEY is not set."

    input_data = {
        "id": ep_data["id"],
        "name": ep_data["name"],
        "templateId": ep_data["templateId"],
        "gpuIds": ep_data["gpuIds"],
        "workersMin": int(min_workers),
        "workersMax": int(max_workers)
    }

    # Process the new Idle Timeout value if provided
    if idle_timeout is not None:
        input_data["idleTimeout"] = int(idle_timeout)
    elif ep_data.get("idleTimeout") is not None:
        input_data["idleTimeout"] = ep_data["idleTimeout"]

    # Safely attach optional configurations to avoid resetting them
    for key in ["locations", "networkVolumeId", "scalerType", "scalerValue"]:
        if ep_data.get(key) is not None:
            input_data[key] = ep_data[key]

    mutation = """
    mutation saveEndpoint($input: EndpointInput!) {
      saveEndpoint(input: $input) {
        id
        name
        workersMin
        workersMax
        idleTimeout
      }
    }
    """
    payload = {"query": mutation, "variables": {"input": input_data}}

    try:
        response = requests.post(GRAPHQL_URL, params=get_params(), json=payload, headers=get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            return None, f"GraphQL Mutation Error: {data['errors'][0].get('message', 'Unknown Error')}"
        return data.get("data", {}).get("saveEndpoint"), None
    except Exception as e:
        return None, f"Update API Error: {str(e)}"